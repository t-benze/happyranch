# Thread Escalation Surfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a thread-dispatched task escalates to the founder, post a `task_escalated` SYSTEM message into the originating thread and re-invoke the dispatching manager — mirroring the existing terminal task-followup. The Feishu escalation notification is unchanged (additive).

**Architecture:** Extract the post+mint+enqueue tail of `_maybe_post_thread_followup` into a shared helper, then add a sibling `_maybe_post_thread_escalation` that resolves the originating thread from a possibly-non-root escalated task (escalations don't cascade, so walk ancestors → revisit chain). Wire it at run_step's two escalation sites. Add a `task_escalated` kind_tag to the three thread renderers and the followup prompt note.

**Tech Stack:** Python 3.12 (FastAPI daemon, Pydantic v2, sqlite via `runtime/infrastructure/database.py`), pytest; React/TS web (`web/`, vitest).

**Spec:** `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md`

**Conventions:** `from __future__ import annotations` at top of every source file; type hints on all signatures; `StrEnum` for enums; run tests with `uv run python -m pytest` (per `memory/pytest-invocation.md`). Run `gitnexus_impact` before editing `_maybe_post_thread_followup` and `_purpose_note`, and `gitnexus_detect_changes` before each commit (per project CLAUDE.md).

---

## File Structure

- **Modify** `runtime/orchestrator/run_step.py`
  - Extract `_append_followup_system_and_reinvoke(...)` (shared tail).
  - Refactor `_maybe_post_thread_followup` to call it (no behavior change).
  - Add `_maybe_post_thread_escalation(orch, task_id, *, reason)`.
  - Call the new helper at the `escalate` branch (~line 339) and the max-steps guard (~line 96).
- **Modify** `runtime/daemon/thread_runner.py` — `_purpose_note` escalation branch.
- **Modify** `runtime/infrastructure/thread_store.py` — transcript renderer `task_escalated` case.
- **Modify** `cli/thread_forward.py` — forward renderer `task_escalated` case.
- **Modify** `web/src/features/threads/ThreadsPage.tsx` — web renderer `task_escalated` case.
- **Modify** `protocol/skills/thread/SKILL.md` — document the escalation followup turn.
- **Modify** `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md` — §9 lists 3 renderers (fix from 2).
- **Test** `tests/test_thread_task_followup.py` — escalation helper unit tests + render tests.
- **Test** `tests/test_run_step.py` — escalate-path + over-budget integration tests.
- **Test** `web/src/features/threads/ThreadsPage.test.tsx` — web render test.

---

## Task 1: Extract the shared post+mint+enqueue tail (refactor, no behavior change)

**Files:**
- Modify: `runtime/orchestrator/run_step.py` (`_maybe_post_thread_followup`, ~lines 1697-1772)
- Test: `tests/test_thread_task_followup.py` (existing tests guard the refactor)

- [ ] **Step 1: Run impact analysis on the function being refactored**

Run: `gitnexus_impact({target: "_maybe_post_thread_followup", direction: "upstream"})`
Report the blast radius to the user. Expected: callers are the terminal sites in `run_step.py`. If HIGH/CRITICAL, warn before proceeding.

- [ ] **Step 2: Confirm the existing followup tests pass (baseline green)**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -v`
Expected: PASS (this is the regression baseline for the refactor).

- [ ] **Step 3: Add the extracted shared-tail helper**

In `runtime/orchestrator/run_step.py`, add this new function immediately **above** `def _maybe_post_thread_followup(`:

```python
def _append_followup_system_and_reinvoke(
    orch: "Orchestrator",
    *,
    thread_id: str,
    dispatcher: str,
    original_id: str,
    source_task_id: str,
    system_payload: dict,
) -> None:
    """Append a SYSTEM message + mint/enqueue a TASK_FOLLOWUP re-invocation.

    Shared tail for `_maybe_post_thread_followup` (terminal) and
    `_maybe_post_thread_escalation`. Race-aware: the atomic cap-projection +
    conditional bump + mint is serialized by the RLock on
    `mint_followup_invocation_with_cap_extend`. `original_id` is the original
    dispatched task id (for audit keying); `source_task_id` is the task that
    triggered this followup (terminal task or escalated task).
    """
    db = orch._db
    audit = orch._audit

    # Append system message (separate from the atomic cap+mint below — the
    # system message ordering relative to concurrent system messages is not
    # part of the atomicity invariant we're protecting).
    from runtime.models import ThreadMessageKind as _TMK
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker=dispatcher,
        kind=_TMK.SYSTEM,
        system_payload=system_payload,
    )

    # Atomic cap-projection + conditional bump + mint.  Closes the TOCTOU race
    # where two concurrent root completions on the same thread both read the
    # same pending count, both skip the bump, both mint, and leave the thread
    # with more obligations than turn_cap.  The @_synchronized RLock on
    # mint_followup_invocation_with_cap_extend serializes all three steps.
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        thread_id=thread_id,
        agent_name=dispatcher,
        triggering_seq=sys_seq,
    )
    if new_cap is not None:
        audit.log_thread_turn_cap_auto_extended(
            thread_id, original_task_id=original_id,
            reason="task_followup", new_cap=new_cap,
        )
    audit.log_thread_task_followup_enqueued(
        thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
        dispatcher=dispatcher, invocation_token=inv.invocation_token,
    )

    # Enqueue onto the org's thread queue. The queue is bound to the daemon's
    # main event loop, but run_step runs on a worker thread, so we cross the
    # loop boundary via run_coroutine_threadsafe — same pattern as
    # `_start_feishu_listeners` uses for cross-thread async bridging.
    import asyncio as _asyncio
    from runtime.daemon.thread_queue import ThreadJob as _ThreadJob
    thread_queue = getattr(orch, "_thread_queue", None)
    main_loop = getattr(orch, "_main_loop", None)
    if thread_queue is not None and main_loop is not None:
        try:
            _asyncio.run_coroutine_threadsafe(
                thread_queue.put(_ThreadJob(
                    org_slug=orch._slug,
                    invocation_token=inv.invocation_token,
                )),
                main_loop,
            )
        except Exception as exc:
            audit.log_thread_followup_skipped(
                thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
                reason="enqueue_failed", detail=str(exc),
            )
    else:
        # Defence: queue or loop not yet wired (e.g., test orchestrator constructed
        # without daemon context). Invocation stays PENDING; audit so the
        # operator can detect it if needed. In production this path is never
        # taken because _lifespan always calls _attach_thread_queue_wiring before
        # the first task step runs.
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
            reason="enqueue_unavailable",
        )
```

- [ ] **Step 4: Replace the inline tail in `_maybe_post_thread_followup` with a call to the helper**

In `_maybe_post_thread_followup`, the `system_payload = { ... }` dict literal is built and ends with `}`. **Delete everything from the line `# Append system message (separate from the atomic cap+mint below — the` through the end of the function** (the entire enqueue block, ending at the `reason="enqueue_unavailable",` block's closing `)`). Replace it with:

```python
    _append_followup_system_and_reinvoke(
        orch,
        thread_id=thread_id,
        dispatcher=dispatcher,
        original_id=original.id,
        source_task_id=task_id,
        system_payload=system_payload,
    )
```

Leave the `system_payload` dict and everything above it unchanged.

- [ ] **Step 5: Run the existing followup tests to verify no behavior change**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -v`
Expected: PASS (all tests that passed in Step 2 still pass — the tail moved but behaves identically).

- [ ] **Step 6: Run the run_step tests (terminal followup is exercised there too)**

Run: `uv run python -m pytest tests/test_run_step.py -v`
Expected: PASS.

- [ ] **Step 7: Detect-changes + commit**

Run: `gitnexus_detect_changes()` — verify only `_maybe_post_thread_followup` and the new `_append_followup_system_and_reinvoke` are affected.

```bash
git add runtime/orchestrator/run_step.py
git commit -m "refactor(run_step): extract shared followup post+mint+enqueue tail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `_maybe_post_thread_escalation` helper

**Files:**
- Modify: `runtime/orchestrator/run_step.py` (new function, after `_append_followup_system_and_reinvoke`)
- Test: `tests/test_thread_task_followup.py` (append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_thread_task_followup.py` (the `orch_with_db`, `_seed_dispatched_root`, and `_payload` helpers already exist earlier in this file; `BlockKind` must be imported — add `BlockKind` to the existing `from runtime.models import ...` line near the top of the Task-7 section if not already present, i.e. change `from runtime.models import TaskRecord, TaskStatus, ThreadRecord, ThreadStatus` to `from runtime.models import BlockKind, TaskRecord, TaskStatus, ThreadRecord, ThreadStatus`):

```python
# ---------------------------------------------------------------------------
# Thread escalation surfacing — _maybe_post_thread_escalation
# Spec: docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md
# ---------------------------------------------------------------------------


def test_escalation_root_fires_and_carries_reason(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="needs founder auth")

    _maybe_post_thread_escalation(orch, "TASK-1", reason="needs founder auth")

    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1
    msgs = orch._db.list_thread_messages("THR-1")
    sysmsgs = [m for m in msgs if m.system_payload
               and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(sysmsgs) == 1
    assert sysmsgs[0].system_payload["reason"] == "needs founder auth"
    assert sysmsgs[0].system_payload["task_id"] == "TASK-1"
    assert sysmsgs[0].speaker == "alice"


def test_escalation_child_depth_surfaces_via_ancestors(orch_with_db):
    """Escalations do NOT cascade; a child-task escalation must still surface
    in the originating thread by walking ancestors to the dispatched root."""
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch, task_id="TASK-1")
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        parent_task_id="TASK-1",
    ))
    orch._db.update_task("TASK-2", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="deep blocker")

    _maybe_post_thread_escalation(orch, "TASK-2", reason="deep blocker")

    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1
    msgs = orch._db.list_thread_messages("THR-1")
    sysmsgs = [m for m in msgs if m.system_payload
               and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(sysmsgs) == 1
    assert sysmsgs[0].system_payload["task_id"] == "TASK-2"
    assert sysmsgs[0].system_payload["original_task_id"] == "TASK-1"


def test_escalation_resolved_in_race_is_noop(orch_with_db):
    """If the task is no longer blocked/escalated (founder resolved in the
    race window), the helper must not post anything."""
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)

    _maybe_post_thread_escalation(orch, "TASK-1", reason="needs founder auth")

    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)
    assert not orch._db.list_thread_messages("THR-1")


def test_escalation_non_thread_task_noop(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    orch._db.insert_task(TaskRecord(
        id="TASK-N", brief="b", team="ops", assigned_agent="alice",
    ))
    orch._db.update_task("TASK-N", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="x")

    _maybe_post_thread_escalation(orch, "TASK-N", reason="x")

    audit_rows = orch._db.get_audit_logs("TASK-N")
    assert not any(r["action"].startswith("thread_") for r in audit_rows)


def test_escalation_thread_not_open_skips_with_audit(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.set_thread_status("THR-1", status=ThreadStatus.ARCHIVED)
    orch._db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="x")

    _maybe_post_thread_escalation(orch, "TASK-1", reason="x")

    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)
    audit_rows = orch._db.get_audit_logs("TASK-1")
    assert any(r["action"] == "thread_followup_skipped" for r in audit_rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k escalation -v`
Expected: FAIL with `ImportError` / `cannot import name '_maybe_post_thread_escalation'`.

- [ ] **Step 3: Implement the helper**

In `runtime/orchestrator/run_step.py`, add immediately **after** `_append_followup_system_and_reinvoke` (and before `_maybe_post_thread_followup`):

```python
def _maybe_post_thread_escalation(
    orch: "Orchestrator",
    task_id: str,
    *,
    reason: str,
) -> None:
    """Post a `task_escalated` SYSTEM message + re-invoke the dispatcher when a
    thread-dispatched task escalates to the founder.

    Unlike `_maybe_post_thread_followup` (terminal-only, root-only because
    terminals cascade up to the parent), escalations do NOT cascade
    (run_step escalate branch: "parent stays blocked(DELEGATED)") and a team
    manager can escalate at any depth. So we walk ancestors to the chain root,
    then the revisit chain, to find the originating thread.

    Spec: docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md
    """
    db = orch._db
    audit = orch._audit

    task = db.get_task(task_id)
    if task is None:
        return
    # Re-read persisted state: the founder may have resolved/cancelled the
    # escalation in the window between try_escalate and this call.
    if not (task.status == TaskStatus.BLOCKED
            and task.block_kind == BlockKind.ESCALATED):
        return

    # Resolve the originating thread. Escalation can fire on a child, so walk
    # ancestors to the chain root first, then the revisit chain (only the
    # dispatched root carries dispatched_from_thread_id).
    from runtime.infrastructure.database import LineageTooDeep  # local: avoid cycle
    try:
        ancestors = db.walk_ancestors(task_id, max_hops=200)
    except LineageTooDeep:
        audit.log_thread_followup_skipped(
            "(unresolved)", original_task_id=task_id, terminal_task_id=task_id,
            reason="chain_too_deep",
        )
        return
    root = ancestors[-1] if ancestors else task
    chain = db.walk_revisit_chain(root.id, max_hops=200, truncate=True)
    original = chain[-1] if chain else root
    thread_id = original.dispatched_from_thread_id
    if thread_id is None:
        # Not a thread-dispatched chain; silent no-op (Feishu path untouched).
        return

    # Thread-state guard.
    thread = db.get_thread(thread_id)
    from runtime.models import ThreadStatus as _ThreadStatus
    if thread is None or thread.status is not _ThreadStatus.OPEN:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open",
            thread_status=(thread.status.value if thread else "missing"),
            task_status="escalated",
        )
        return

    # Dispatcher identity from the thread_dispatch audit row on the original.
    dispatch_rows = [
        r for r in db.get_audit_logs(thread_id)
        if r["action"] == "thread_dispatch"
        and _payload_dict(r).get("task_id") == original.id
    ]
    if not dispatch_rows:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="dispatcher_unresolved",
        )
        return
    dispatcher = _payload_dict(dispatch_rows[0])["dispatcher"]

    system_payload = {
        "kind_tag": "task_escalated",
        "task_id": task_id,
        "original_task_id": original.id,
        "root_task_id": root.id,
        "status": "escalated",
        "reason": reason,
        "revisit_chain_length": len(chain) if chain else 1,
    }
    _append_followup_system_and_reinvoke(
        orch,
        thread_id=thread_id,
        dispatcher=dispatcher,
        original_id=original.id,
        source_task_id=task_id,
        system_payload=system_payload,
    )
```

Note: `TaskStatus`, `BlockKind`, and `_payload_dict` are already imported/defined in `run_step.py` (used by surrounding code). If a NameError surfaces for `BlockKind`, add it to the existing top-of-file `from runtime.models import ...` line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k escalation -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Detect-changes + commit**

Run: `gitnexus_detect_changes()`

```bash
git add runtime/orchestrator/run_step.py tests/test_thread_task_followup.py
git commit -m "feat(threads): add _maybe_post_thread_escalation helper

Posts task_escalated SYSTEM message + re-invokes the dispatcher for a
thread-dispatched task that escalates, at any task depth.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Wire the helper at run_step's two escalation sites

**Files:**
- Modify: `runtime/orchestrator/run_step.py` (escalate branch ~line 339; max-steps guard ~line 96)
- Test: `tests/test_run_step.py` (append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_step.py`. These mirror the existing `test_run_step_escalate_parks_blocked_and_leaves_parent_parked` and `test_run_step_over_budget_parks_escalated` patterns, but seed a thread dispatch so the new surfacing fires. Add the import helpers at the top of each test as shown.

```python
def _seed_open_thread_dispatch(db, *, thread_id, task_id, dispatcher, target):
    from runtime.models import ThreadRecord
    db.insert_thread(ThreadRecord(id=thread_id, subject="t"))
    db.add_thread_participant(thread_id, dispatcher, added_by="founder")
    from runtime.infrastructure.audit_logger import AuditLogger
    AuditLogger(db).log_thread_dispatch(
        thread_id, task_id=task_id, dispatcher=dispatcher,
        target_agent=target, team="engineering",
    )


def test_run_step_escalate_surfaces_in_thread(runtime, db, monkeypatch):
    import json
    from runtime.models import ThreadInvocationPurpose
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-9",
    ))
    _seed_open_thread_dispatch(db, thread_id="THR-9", task_id="T-1",
                               dispatcher="engineering_head", target="engineering_head")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()  # mirror existing escalate test; not used on escalate path

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder auth"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED and t.block_kind == BlockKind.ESCALATED
    msgs = db.list_thread_messages("THR-9")
    esc = [m for m in msgs if m.system_payload
           and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(esc) == 1
    assert esc[0].system_payload["reason"] == "needs founder auth"
    invs = db.list_thread_invocations("THR-9")
    assert any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)


def test_run_step_over_budget_surfaces_in_thread(runtime, db):
    from runtime.models import ThreadInvocationPurpose
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-9",
    ))
    db.update_task("T-1", orchestration_step_count=3)  # already at the cap
    _seed_open_thread_dispatch(db, thread_id="THR-9", task_id="T-1",
                               dispatcher="engineering_head", target="engineering_head")

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch.run_step("T-1")

    msgs = db.list_thread_messages("THR-9")
    esc = [m for m in msgs if m.system_payload
           and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(esc) == 1
    assert "max steps" in esc[0].system_payload["reason"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_run_step.py -k "surfaces_in_thread" -v`
Expected: FAIL — no `task_escalated` message is posted yet (assertion `len(esc) == 1` fails with 0).

- [ ] **Step 3: Wire the `escalate` decision branch**

In `runtime/orchestrator/run_step.py`, in the `if decision.action == "escalate":` block, **after** the existing `orch.notify_escalated(...)` call and **before** the `# parent stays blocked(DELEGATED)` comment / `return`, add:

```python
        _maybe_post_thread_escalation(orch, task_id, reason=reason)
```

The block becomes:

```python
        orch._audit.log_escalation(task_id, agent, reason)
        orch.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=getattr(report, "output_summary", "") or "",
        )
        _maybe_post_thread_escalation(orch, task_id, reason=reason)
        # parent stays blocked(DELEGATED) until this task reaches a terminal.
        return
```

- [ ] **Step 4: Wire the max-steps budget guard**

In the `# ---- 2. Budget guard ----` block, **after** the existing `orch.notify_escalated(...)` call and **before** the `return`, add the same call. The block becomes:

```python
        orch._audit.log_escalation(task_id, "orchestrator", reason)
        orch.notify_escalated(
            task_id=task_id, agent="orchestrator", reason=reason,
        )
        _maybe_post_thread_escalation(orch, task_id, reason=reason)
        return
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_run_step.py -k "surfaces_in_thread" -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full run_step + followup suites for regressions**

Run: `uv run python -m pytest tests/test_run_step.py tests/test_thread_task_followup.py -v`
Expected: PASS.

- [ ] **Step 7: Detect-changes + commit**

Run: `gitnexus_detect_changes()`

```bash
git add runtime/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(threads): surface escalations in originating thread at both escalate sites

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Escalation-aware followup prompt note

**Files:**
- Modify: `runtime/daemon/thread_runner.py` (`_purpose_note`, ~lines 115-127)
- Test: `tests/test_thread_task_followup.py` (append)

- [ ] **Step 1: Run impact analysis**

Run: `gitnexus_impact({target: "_purpose_note", direction: "upstream"})`
Report blast radius. Expected: callers are `thread_runner.py` invocation builders. Warn if HIGH/CRITICAL.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_thread_task_followup.py`. The existing `test_purpose_note_task_followup_renders_task_id_and_status` (line ~92) shows the calling shape; mirror it. Inspect that test first to reuse its `ThreadMessage` construction helper. Add:

```python
def test_purpose_note_escalated_uses_escalation_wording():
    from runtime.daemon.thread_runner import _purpose_note
    from runtime.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    msg = ThreadMessage(
        id="m1", thread_id="THR-1", seq=5, speaker="alice",
        kind=ThreadMessageKind.SYSTEM,
        body_markdown=None, decline_reason=None,
        system_payload={
            "kind_tag": "task_escalated",
            "task_id": "TASK-893",
            "status": "escalated",
            "reason": "needs founder CDN authorize",
        },
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
    )
    note = _purpose_note("task_followup", 5, "alice", triggering_message=msg)
    assert "ESCALATED" in note
    assert "TASK-893" in note
    assert "needs founder CDN authorize" in note
    assert "resolve the escalation yourself" in note
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k escalated_uses_escalation_wording -v`
Expected: FAIL (current note says "reached `escalated`", not the escalation-specific wording; `assert "resolve the escalation yourself" in note` fails).

- [ ] **Step 4: Implement the branch**

In `runtime/daemon/thread_runner.py`, inside `_purpose_note`, the `if purpose == "task_followup":` block currently reads `task_id` and `status` then returns the terminal wording. Add an escalation branch **before** the existing `return`:

```python
    if purpose == "task_followup":
        payload = (triggering_message.system_payload or {}) if triggering_message else {}
        task_id = payload.get("task_id", "?")
        status = payload.get("status", "?")
        if status == "escalated":
            reason = (payload.get("reason") or "").strip()
            reason_clause = f': "{reason[:240]}"' if reason else ""
            return (
                f"Task {task_id} that you dispatched from this thread has "
                f"ESCALATED to the founder{reason_clause}. The task is blocked "
                f"awaiting a founder decision. Post a concise reply in this "
                f"thread that states what you need from the founder and why, so "
                f"she sees it in context (pull details via `happyranch details "
                f"{task_id}`). Do not attempt to resolve the escalation "
                f"yourself; do not dispatch a new task from this turn. Decline "
                f"if the Feishu escalation already says everything and a thread "
                f"restatement adds nothing."
            )
        return (
            f"Task {task_id} that you dispatched from this thread reached "
            f"`{status}`. Compose a follow-up reply with the result (pull "
            f"details via `happyranch details {task_id}`), or decline if "
            f"there is nothing substantive to add. Dispatching a new task "
            f"from this turn is not allowed; mention any new action in the "
            f"reply and let the founder loop in."
        )
```

(Only the `if status == "escalated":` branch is new; leave the trailing terminal-wording `return` exactly as it was.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k "purpose_note" -v`
Expected: PASS (the new test and the pre-existing `test_purpose_note_task_followup_renders_task_id_and_status`).

- [ ] **Step 6: Detect-changes + commit**

Run: `gitnexus_detect_changes()`

```bash
git add runtime/daemon/thread_runner.py tests/test_thread_task_followup.py
git commit -m "feat(threads): escalation-aware task_followup prompt note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Renderers (transcript, forward, web)

**Files:**
- Modify: `runtime/infrastructure/thread_store.py` (~line 103, after `task_failed` case)
- Modify: `cli/thread_forward.py` (~line 50, after `task_failed` case)
- Modify: `web/src/features/threads/ThreadsPage.tsx` (~line 530, after `task_failed` case)
- Test: `tests/test_thread_task_followup.py` (append py render tests); `web/src/features/threads/ThreadsPage.test.tsx` (append web test)

- [ ] **Step 1: Write the failing Python render tests**

Append to `tests/test_thread_task_followup.py` (the `_make_system_msg` helper used by the existing render tests is already defined earlier in this file):

```python
def test_thread_store_renders_task_escalated():
    from runtime.infrastructure.thread_store import render_transcript_body

    msg = _make_system_msg(
        12,
        {
            "kind_tag": "task_escalated",
            "task_id": "TASK-893",
            "original_task_id": "TASK-893",
            "root_task_id": "TASK-893",
            "status": "escalated",
            "reason": "needs founder CDN authorize",
            "revisit_chain_length": 1,
        },
    )
    out = render_transcript_body([msg])
    assert "Task TASK-893 escalated" in out
    assert "needs founder CDN authorize" in out


def test_thread_forward_renders_task_escalated():
    from cli.thread_forward import build_forward_body_from_thread
    from runtime.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    msg = ThreadMessage(
        id="m1", thread_id="THR-1", seq=12, speaker="alice",
        kind=ThreadMessageKind.SYSTEM, body_markdown=None, decline_reason=None,
        system_payload={
            "kind_tag": "task_escalated",
            "task_id": "TASK-2",
            "original_task_id": "TASK-1",
            "status": "escalated",
            "reason": "deep blocker",
            "revisit_chain_length": 1,
        },
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
    )
    out = build_forward_body_from_thread(
        source_id="THR-1", messages=[msg], subject="s",
    )
    assert "Task TASK-2 escalated" in out
    assert "chain root TASK-1" in out
    assert "deep blocker" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k "renders_task_escalated" -v`
Expected: FAIL (no `task_escalated` case; renderers fall through to the `else` generic `system: task_escalated`, so the `escalated` / `chain root` / reason assertions fail).

- [ ] **Step 3: Implement the transcript renderer case**

In `runtime/infrastructure/thread_store.py`, add **after** the `elif tag == "task_failed":` block (which ends just before `elif tag == "turn_cap_extended":`):

```python
            elif tag == "task_escalated":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                rendered = f"**Task {tid} escalated**" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                reason = (payload.get("reason") or "").strip()
                if reason:
                    rendered += f" · {reason[:240]}"
```

- [ ] **Step 4: Implement the forward renderer case**

In `cli/thread_forward.py`, add **after** the `elif tag == "task_failed":` block (just before the `else:` that appends the generic `system: {tag}`):

```python
            elif tag == "task_escalated":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} escalated" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                reason = (payload.get("reason") or "").strip()
                if reason:
                    label += f": {reason[:240]}"
                rendered.append(f"> (system: {label})")
```

- [ ] **Step 5: Run the Python render tests to verify they pass**

Run: `uv run python -m pytest tests/test_thread_task_followup.py -k "renders_task_escalated" -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Write the failing web render test**

In `web/src/features/threads/ThreadsPage.test.tsx`, inside the `describe('ThreadsPage — system message rendering', ...)` block, append after the `task_failed` test:

```tsx
  test('renders task_escalated system message with task id and reason', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-012', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_escalated',
        task_id: 'TASK-893',
        original_task_id: 'TASK-893',
        status: 'escalated',
        reason: 'needs founder CDN authorize',
        revisit_chain_length: 1,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-012`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-893/)).toBeInTheDocument();
      expect(screen.getByText(/needs founder CDN authorize/)).toBeInTheDocument();
    });
  });
```

- [ ] **Step 7: Run the web test to verify it fails**

Run: `cd web && npx vitest run src/features/threads/ThreadsPage.test.tsx -t "task_escalated"`
Expected: FAIL (no `task_escalated` case; reason text not rendered).

- [ ] **Step 8: Implement the web renderer case**

In `web/src/features/threads/ThreadsPage.tsx`, add **after** the `case 'task_failed': { ... }` block:

```tsx
    case 'task_escalated': {
      const taskId = String(payload.task_id ?? '');
      const taskLink = slug && taskId
        ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
        : taskId;
      const reason = payload.reason ? String(payload.reason).slice(0, 240) : null;
      return (
        <>
          task {taskLink} escalated{reason ? ` · ${reason}` : ''}
        </>
      );
    }
```

- [ ] **Step 9: Run the web test to verify it passes**

Run: `cd web && npx vitest run src/features/threads/ThreadsPage.test.tsx`
Expected: PASS (the new test and all pre-existing system-message tests).

- [ ] **Step 10: Detect-changes + commit**

Run: `gitnexus_detect_changes()`

```bash
git add runtime/infrastructure/thread_store.py cli/thread_forward.py \
        web/src/features/threads/ThreadsPage.tsx \
        tests/test_thread_task_followup.py web/src/features/threads/ThreadsPage.test.tsx
git commit -m "feat(threads): render task_escalated in transcript, forward, and web

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Docs — agent skill + spec renderer-count fix

**Files:**
- Modify: `protocol/skills/thread/SKILL.md` (Task-followup turn section, ~lines 137-149)
- Modify: `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md` (§9)

- [ ] **Step 1: Update the agent skill doc**

In `protocol/skills/thread/SKILL.md`, in the `## Task-followup turn` section, after the paragraph that begins "This is a `task_followup` turn ...", add a new paragraph:

```markdown
**Escalation variant:** If the dispatched task **escalated** to the founder instead
of finishing, the thread gets a `task_escalated` system message (with the escalation
reason) and the prompt-header asks you to restate the ask in-thread. In that turn:
state concisely what you need from the founder and why — do NOT try to resolve the
escalation yourself, and do NOT dispatch a new task. Decline if the Feishu escalation
already covers it and a thread restatement adds nothing.
```

- [ ] **Step 2: Fix the spec renderer count**

In `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md`, §9 "Renderers", change the opening sentence from "the two thread system-message renderers" to "the three thread system-message renderers" and add a bullet for the forward renderer:

```markdown
- **`cli/thread_forward.py`** (forward body, after `task_failed`): mirror `task_failed`:
  `Task TASK-NNN escalated` + ` (chain root TASK-MMM)` when root differs + `: {reason[:240]}`.
```

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/thread/SKILL.md docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md
git commit -m "docs(threads): document task_escalated turn; fix spec renderer count

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python unit suite**

Run: `uv run python -m pytest tests/ -v`
Expected: PASS (no regressions). Pay attention to `tests/test_run_step.py`, `tests/test_thread_task_followup.py`, `tests/test_thread_store.py`, `tests/daemon/test_threads_routes.py`.

- [ ] **Step 2: Confirm the OpenAPI snapshot is unchanged**

Run: `uv run python -m pytest tests/contract/test_openapi_snapshot.py -v`
Expected: PASS with no diff — this change adds no route (the threads stream already carries SYSTEM payloads generically). If it fails, STOP and report: it means an unexpected route surface changed.

- [ ] **Step 3: Run the web test suite**

Run: `cd web && npx vitest run`
Expected: PASS, including `src/features/threads/ThreadsPage.test.tsx` and `src/test/openapi-coverage.test.ts`.

- [ ] **Step 4: Run integration tests touching threads/run_step**

Run: `uv run python -m pytest tests/integration/test_thread_task_followup_e2e.py -v -m integration`
Expected: PASS. (These spawn a real daemon + fake CLIs; confirms the thread-queue wiring the escalation path reuses still functions.)

- [ ] **Step 5: Final detect-changes**

Run: `gitnexus_detect_changes()`
Expected: only the symbols/files touched across Tasks 1-6. Report the summary.

- [ ] **Step 6: Confirm clean tree**

Run: `git status`
Expected: clean (all changes committed across Tasks 1-6).

---

## Notes for the implementer

- **Why a separate helper, not extending `_maybe_post_thread_followup`:** the terminal helper is root-only because terminal failures cascade up to the parent, which re-enters it at the root. Escalations do not cascade (the parent stays `blocked/delegated`), so a child escalation would never reach the root via cascade — hence the explicit `walk_ancestors` in the escalation helper and the deliberate absence of a root-only gate.
- **The SYSTEM message carries the full `reason`** so the escalation is visible in-thread even if the re-invocation later no-ops or fails — do not drop it.
- **Re-escalation:** a task that escalates, is resolved, runs again, and re-escalates posts a new `task_escalated` each time. `db.try_escalate` is a CAS that transitions once per escalation event, and the helper is called once per transition, so there is no duplicate post for a single event. This is intended (each escalation is a distinct founder-attention event) — do not add dedup.
- **Do not touch** `notify_escalated` / the Feishu path / `resolve-escalation` — the thread post is purely additive.
