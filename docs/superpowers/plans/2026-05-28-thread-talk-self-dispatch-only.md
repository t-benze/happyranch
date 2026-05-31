# Thread / Talk Self-Dispatch-Only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Constrain `/threads/{id}/dispatch` and `/talks/{id}/dispatch` to self-only — the dispatcher must equal the target agent. Removes the existing manager exemption that lets a manager push work onto workers from inside a thread/talk; workers were already restricted to self-dispatch.

**Architecture:** Single route-level guard in each of the two existing dispatch handlers. No data-model change. The pre-existing `worker_must_self_dispatch` rule is renamed to a unified `thread_dispatch_must_be_self` / `talk_dispatch_must_be_self`; the manager-branch checks (`target_not_in_team`, `cross_team_dispatch_forbidden`) become dead code under the new rule and are removed. A shared hint constant explains the doctrine (use compose for cross-agent work; self-dispatch a manager root for iterative phase work) in the rejection envelope.

**Tech Stack:** Python 3.13, FastAPI, pydantic v2, SQLite. Tests use pytest with the existing daemon fixtures (`client_with_runtime` for talks, `app`/`org_state`/`auth_headers` for threads).

**Spec:** `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`

---

## File Map

**Create:**
- `src/daemon/routes/_doctrine.py` — shared `SELF_DISPATCH_HINT` constant.
- `tests/integration/test_thread_self_dispatch_phase_e2e.py` — single e2e for the phase-via-self-dispatch pattern.

**Modify:**
- `src/daemon/routes/threads.py` (lines 856-876) — collapse manager/worker branching to single self-only check + rename error codes.
- `src/daemon/routes/talks.py` (lines 314-338) — mirror the same change.
- `tests/daemon/test_threads_routes.py` — rename error code in `test_worker_cannot_dispatch_to_other_agent`; add manager-rejection test.
- `tests/daemon/test_talks_dispatch.py` — rename error code; convert `test_manager_dispatches_to_team_worker` from happy-path to rejection; convert `test_manager_target_not_in_team` to rejection-with-new-code (or delete as redundant); update `test_dispatch_cross_team_forbidden` to new code.
- `protocol/skills/thread/SKILL.md` — doctrine section.
- `protocol/skills/talk/SKILL.md` — doctrine section.
- `protocol/skills/dispatch/SKILL.md` — one-sentence cross-reference at the top.
- `CLAUDE.md` — new "Thread / talk dispatch self-only rule" invariants subsection.

**No changes:**
- Database schema, audit payload shape, OpenAPI snapshot, web UI.

---

## Task 1: Add shared doctrine hint constant

**Files:**
- Create: `src/daemon/routes/_doctrine.py`

- [ ] **Step 1: Create the module**

```python
# src/daemon/routes/_doctrine.py
"""Shared doctrine strings surfaced in route error envelopes.

Centralized so threads and talks return identical hint text and stay in sync
when the wording evolves.
"""
from __future__ import annotations

SELF_DISPATCH_HINT = (
    "Threads (and talks) only accept self-dispatch.\n\n"
    "For cross-agent work, either:\n"
    "  (a) self-dispatch a manager root and delegate internally via the\n"
    "      manager-decision loop (recommended for iterative phase work), or\n"
    "  (b) use `happyranch threads compose --to <other-agent>` to address\n"
    "      the other agent (or their team's manager) as a thread message,\n"
    "      and let them drive their own work.\n\n"
    "Cross-team handoffs always route through compose, not dispatch."
)
```

- [ ] **Step 2: Commit**

```bash
git add src/daemon/routes/_doctrine.py
git commit -m "feat(routes): add shared SELF_DISPATCH_HINT constant for doctrine surfacing"
```

---

## Task 2: Add failing test for thread manager-to-worker rejection

**Files:**
- Modify: `tests/daemon/test_threads_routes.py` (add new test after `test_worker_cannot_dispatch_to_other_agent` at line 288)

- [ ] **Step 1: Add the test**

Insert immediately after the existing `test_worker_cannot_dispatch_to_other_agent` function:

```python
def test_manager_cannot_dispatch_to_team_worker(tmp_home, app, org_state, auth_headers):
    """The manager exemption from the self-dispatch rule is removed.

    A manager attempting to thread-dispatch a worker in their own team is
    rejected with thread_dispatch_must_be_self. The fix for THR-010 (founder
    diagnosis 2026-05-28): managers must self-dispatch a phase root and
    delegate internally via the manager-decision loop.
    """
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    _seed_agent(org_state, "dev_agent")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head", "target_agent": "dev_agent",
              "brief": "do x"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "thread_dispatch_must_be_self"
    assert detail["dispatcher"] == "engineering_head"
    assert detail["requested_target"] == "dev_agent"
    assert "compose" in detail["hint"].lower()


def test_manager_self_dispatch_from_thread_succeeds(tmp_home, app, org_state, auth_headers):
    """Manager dispatching with target_agent omitted (or set to self) is allowed."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "drive web-app v1 phase"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_agent"] == "engineering_head"
```

- [ ] **Step 2: Verify `_seed_agent` supports `role="manager"`**

Run: `grep -n "def _seed_agent" tests/daemon/test_threads_routes.py`

If the helper does not accept a `role` argument, either extend it or use the existing helper that registers managers in the teams registry. Check by reading the helper definition; if needed, add the kwarg with default `role="worker"` and wire to the existing teams-registry mutation already used in talks tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_threads_routes.py::test_manager_cannot_dispatch_to_team_worker tests/daemon/test_threads_routes.py::test_manager_self_dispatch_from_thread_succeeds -v`

Expected: `test_manager_cannot_dispatch_to_team_worker` FAILS — the current code returns 200 because the manager exemption is still in place. `test_manager_self_dispatch_from_thread_succeeds` may pass or fail depending on test data (mostly should pass; if it fails, fix the test data before moving on, since this test must already pass once the rule is in place).

- [ ] **Step 4: Commit (red)**

```bash
git add tests/daemon/test_threads_routes.py
git commit -m "test(threads): add failing tests for manager self-dispatch-only rule"
```

---

## Task 3: Tighten thread dispatch route to self-only

**Files:**
- Modify: `src/daemon/routes/threads.py` (lines 856-876 — the role-based assignment block inside `dispatch_from_thread_endpoint`)

- [ ] **Step 1: Read the current block to confirm the exact lines**

Read `src/daemon/routes/threads.py:840-877` to confirm the block starts at `effective_target = body.target_agent if body.target_agent is not None else dispatcher` and ends after the `if is_manager:` team-membership branch.

- [ ] **Step 2: Replace the block**

Replace lines 856-876 (the block from `effective_target = ...` through the end of the `if is_manager:` branch) with:

```python
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={"code": "thread_dispatch_must_be_self",
                        "dispatcher": dispatcher,
                        "requested_target": effective_target,
                        "hint": SELF_DISPATCH_HINT},
            )
```

Update the `effective_team` block (lines 848-855, the existing `cross_team_dispatch_forbidden` check) to use the new error code name:

```python
        effective_team = body.team if body.team is not None else dispatcher_team
        if effective_team != dispatcher_team:
            raise HTTPException(
                status_code=403,
                detail={"code": "thread_dispatch_team_override_forbidden",
                        "dispatcher_team": dispatcher_team,
                        "requested_team": effective_team,
                        "hint": SELF_DISPATCH_HINT},
            )
```

Add the import at the top of `routes/threads.py` near the other relative imports:

```python
from src.daemon.routes._doctrine import SELF_DISPATCH_HINT
```

The `is_manager` local computed at line 841 is still needed downstream for the `dispatcher_role` audit field — leave that line and the `dispatcher_team` resolution untouched.

- [ ] **Step 3: Run the new tests to verify they pass**

Run: `uv run pytest tests/daemon/test_threads_routes.py::test_manager_cannot_dispatch_to_team_worker tests/daemon/test_threads_routes.py::test_manager_self_dispatch_from_thread_succeeds -v`

Expected: both PASS.

- [ ] **Step 4: Rename the existing worker rejection test's error code assertion**

Edit `tests/daemon/test_threads_routes.py` line 287:

```python
# Before
assert resp.json()["detail"]["code"] == "worker_must_self_dispatch"
# After
assert resp.json()["detail"]["code"] == "thread_dispatch_must_be_self"
```

- [ ] **Step 5: Run the full thread-route test file**

Run: `uv run pytest tests/daemon/test_threads_routes.py -v`

Expected: all tests pass. If any test fails because it depended on the old error code or the old manager-can-dispatch-anywhere behavior, update the assertion to match the new behavior — but DO NOT loosen the new rule.

- [ ] **Step 6: Commit (green)**

```bash
git add src/daemon/routes/threads.py tests/daemon/test_threads_routes.py
git commit -m "feat(threads): tighten /dispatch to self-only; remove manager exemption"
```

---

## Task 4: Update talks dispatch tests to expect self-only rejection

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py` lines 180-260

- [ ] **Step 1: Rename the worker test's error code assertion**

Line 208:

```python
# Before
assert detail["code"] == "worker_must_self_dispatch"
# After
assert detail["code"] == "talk_dispatch_must_be_self"
```

- [ ] **Step 2: Rename `test_dispatch_cross_team_forbidden`**

Line 190:

```python
# Before
assert detail["code"] == "cross_team_dispatch_forbidden"
# After
assert detail["code"] == "talk_dispatch_team_override_forbidden"
```

- [ ] **Step 3: Convert `test_manager_dispatches_to_team_worker` to rejection**

Replace the body of the test (lines 216-241) with:

```python
def test_manager_cannot_dispatch_to_team_worker(client_with_runtime):
    """Manager exemption removed: managers may only self-dispatch from a talk.

    Replaces the prior happy-path test; the THR-010 founder diagnosis
    (2026-05-28) made this rejection the intended behavior. Cross-agent work
    routes via `happyranch threads compose`, not via dispatch.
    """
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    _seed_workspace(state, "engineering_head")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "implement X", "target_agent": "dev_agent"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_must_be_self"
    assert detail["dispatcher"] == "engineering_head"
    assert detail["requested_target"] == "dev_agent"
    assert "compose" in detail["hint"].lower()
```

- [ ] **Step 4: Convert `test_manager_target_not_in_team` to redundancy proof OR delete**

The self-only rule subsumes this case (a manager can no longer target anyone other than self, in-team or not). Replace lines 244-259 with a single rejection assertion under the new code, OR delete the test if you prefer:

```python
def test_manager_cannot_dispatch_cross_team(client_with_runtime):
    """Manager dispatching to an out-of-team agent is rejected by the unified
    self-only rule (not the old `target_not_in_team` branch, which was removed
    as dead code under the new rule)."""
    client, state = client_with_runtime
    _seed_workspace(state, "engineering_head")
    _seed_workspace(state, "content_writer")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "content_writer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_must_be_self"
```

- [ ] **Step 5: Add manager-self-dispatch happy-path test**

Add immediately after the rejection tests:

```python
def test_manager_self_dispatch_from_talk_succeeds(client_with_runtime):
    """Manager dispatching with target omitted (defaults to self) is allowed."""
    client, state = client_with_runtime
    _seed_workspace(state, "engineering_head")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "drive phase X"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "engineering_head"
    assert body["team"] == "engineering"
```

- [ ] **Step 6: Run the talks tests — verify all FAIL except the new manager-self happy-path (which may pass since today's code does allow it)**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py -v`

Expected: rejection tests FAIL because the talks route still returns the old codes / still allows manager-to-worker. The new happy-path test passes because manager-self has always been allowed.

- [ ] **Step 7: Commit (red)**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talks): update tests for self-only dispatch rule"
```

---

## Task 5: Tighten talks dispatch route to self-only

**Files:**
- Modify: `src/daemon/routes/talks.py` lines 285-338

- [ ] **Step 1: Add import**

Near the existing imports at the top of the file:

```python
from src.daemon.routes._doctrine import SELF_DISPATCH_HINT
```

- [ ] **Step 2: Replace the role-based assignment block**

Replace lines 313-338 (from `# 5. Resolve effective_target + role-based assignment rule.` through the end of the `if is_manager:` block) with:

```python
        # 5. Self-only dispatch rule (managers and workers alike).
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "talk_dispatch_must_be_self",
                    "dispatcher": dispatcher,
                    "requested_target": effective_target,
                    "hint": SELF_DISPATCH_HINT,
                },
            )
```

- [ ] **Step 3: Rename the `cross_team_dispatch_forbidden` code**

Replace lines 303-311 (the existing `cross_team_dispatch_forbidden` block):

```python
        effective_team = body.team if body.team is not None else dispatcher_team
        if effective_team != dispatcher_team:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "talk_dispatch_team_override_forbidden",
                    "dispatcher_team": dispatcher_team,
                    "requested_team": effective_team,
                    "hint": SELF_DISPATCH_HINT,
                },
            )
```

The `is_manager` local computed at line 290 is still needed for the `dispatcher_role` audit field (line 363) — leave the boolean and `dispatcher_team` lookup untouched.

- [ ] **Step 4: Run the talks tests — verify they pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit (green)**

```bash
git add src/daemon/routes/talks.py
git commit -m "feat(talks): tighten /dispatch to self-only; remove manager exemption"
```

---

## Task 6: Integration test — manager self-dispatches a phase root, delegates internally

**Files:**
- Create: `tests/integration/test_thread_self_dispatch_phase_e2e.py`

- [ ] **Step 1: Read an existing integration test for shape**

Read `tests/integration/test_threads_e2e.py` (skim ~100 lines) to see how `fake_claude_plan_env` and `fake_claude_thread_plan_env` are wired for an end-to-end thread test that also exercises a dispatched task. Mirror that structure.

- [ ] **Step 2: Write the test**

```python
# tests/integration/test_thread_self_dispatch_phase_e2e.py
"""End-to-end: manager self-dispatches a phase root from a thread reply,
the phase root delegates to a worker via the manager-decision loop, the
worker completes, the phase root completes, and a single TASK_FOLLOWUP
lands in the originating thread.

Validates the doctrine codified by docs/superpowers/specs/
2026-05-28-thread-talk-self-dispatch-only-design.md: iterative work lives
in task trees, threads see only the start + end of a phase.
"""
import pytest

pytestmark = pytest.mark.integration


def test_manager_self_dispatch_phase_root(
    daemon_server, fake_claude_plan_env, fake_claude_thread_plan_env, opc,
):
    # 1. Founder composes a thread to engineering_head.
    # 2. fake_claude_thread_plan_env: on the manager's BOOTSTRAP/REPLY turn,
    #    the manager calls `happyranch threads dispatch` with no target
    #    (defaults to self) and brief "drive phase X".
    # 3. fake_claude_plan_env: the resulting task (phase root) runs a
    #    manager-decision loop that delegates to a worker (`dev_agent`),
    #    waits for the worker terminal, then emits "done".
    # 4. The phase root reaches COMPLETED → _maybe_post_thread_followup
    #    fires once → thread receives one task_completed SYSTEM message and
    #    one TASK_FOLLOWUP invocation.
    # 5. fake_claude_thread_plan_env: on the TASK_FOLLOWUP turn, the
    #    manager posts a reply summarizing the phase.
    # 6. Assertions:
    #    - Thread transcript has exactly one task_dispatched SYSTEM message
    #      (the manager's self-dispatch, NOT the internal delegation).
    #    - Thread transcript has exactly one task_completed SYSTEM message.
    #    - Thread has the manager's followup reply.
    #    - The delegated child task (worker) is NOT visible in the thread.

    # Fill in plan-env scripts + assertions per the structure observed in
    # tests/integration/test_threads_e2e.py.
    ...
```

The full body needs to be filled in with the same idioms as `test_threads_e2e.py::test_agent_dispatch_from_thread_creates_task`. Do not invent new fixtures or helpers — reuse what's there.

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_thread_self_dispatch_phase_e2e.py -v -m integration`

Expected: PASS once the self-dispatch + delegation + followup chain completes correctly.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_thread_self_dispatch_phase_e2e.py
git commit -m "test(integration): manager self-dispatch phase root with internal delegation"
```

---

## Task 7: Update agent skill docs with the doctrine

**Files:**
- Modify: `protocol/skills/thread/SKILL.md`
- Modify: `protocol/skills/talk/SKILL.md`
- Modify: `protocol/skills/dispatch/SKILL.md`

- [ ] **Step 1: Read the current `protocol/skills/thread/SKILL.md`**

Look for the existing section on dispatch (search for "dispatch" or "compose").

- [ ] **Step 2: Add doctrine section to `protocol/skills/thread/SKILL.md`**

Insert after the existing dispatch-related section (or at the end if none exists):

```markdown
## Dispatch from a thread is self-only

When you are participating in a thread (REPLY / BOOTSTRAP turn), `happyranch
threads dispatch` may only target **yourself**. The runtime rejects any other
target with `thread_dispatch_must_be_self`.

This is intentional. Threads exist for founder-visible coordination and
cross-team handoffs. Iterative work (review → revise → re-review, fan-out
to multiple sub-tasks) belongs inside a task tree, where the manager-decision
loop handles delegation natively.

### Patterns

- **Phase work in your own team:** self-dispatch a root task with a phase
  brief. If you are a manager, your manager-decision loop drives delegation
  to workers internally. The thread sees one `task_completed` /
  `task_failed` system message and one TASK_FOLLOWUP turn at the end.

- **Loop in another agent in your team:** use `happyranch threads compose
  --to <agent>` or `happyranch threads invite`. They receive a thread
  invocation (BOOTSTRAP or REPLY) and decide what to do with it.

- **Cross-team handoff:** use `happyranch threads compose --to
  <other-team-manager>` — possibly opening a new thread for the cross-team
  subject. Their manager receives a BOOTSTRAP turn and self-dispatches if
  they take the work on.

If you see `thread_dispatch_must_be_self` (or `talk_dispatch_must_be_self`)
in an error envelope: you tried to push work onto another agent from inside
a thread or talk. Re-route via compose, or self-dispatch and own the phase.
```

- [ ] **Step 3: Add the same doctrine (adapted) to `protocol/skills/talk/SKILL.md`**

Talks are 1:1 so the cross-agent message option is "end the talk and open a thread" rather than `compose --to`. Adapt accordingly — single section, 8-10 lines.

- [ ] **Step 4: Add cross-reference to `protocol/skills/dispatch/SKILL.md`**

One sentence at the top:

```markdown
> **Self-only from thread / talk:** Inside a thread or talk turn, this command
> may only target yourself. See `protocol/skills/thread/SKILL.md` for the
> doctrine and `happyranch threads compose` for cross-agent work.
```

- [ ] **Step 5: Commit**

```bash
git add protocol/skills/thread/SKILL.md protocol/skills/talk/SKILL.md protocol/skills/dispatch/SKILL.md
git commit -m "docs(skills): document self-only dispatch rule for thread/talk turns"
```

---

## Task 8: Update CLAUDE.md with invariants subsection

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate insertion point**

Find the existing "## Thread task-followup" subsection (it's grouped with the other thread/talk invariants). Insert the new subsection immediately after it.

- [ ] **Step 2: Insert the subsection**

```markdown
## Thread / talk dispatch self-only rule

Both `/threads/{id}/dispatch` and `/talks/{id}/dispatch` reject any call
where `effective_target != dispatcher`. The doctrine is "threads/talks are
coordination surfaces; iterative work lives in task trees." Spec:
`docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

**Non-obvious invariants:**

- The rule applies uniformly to managers AND workers. Pre-2026-05-28
  history: workers were already restricted (`worker_must_self_dispatch`);
  managers were exempted. THR-010 surfaced the exemption as a footgun. The
  new code collapses both paths into a single check.
- `target_not_in_team` (manager branch) is unreachable under the new rule
  and has been removed from `routes/threads.py` and `routes/talks.py`. Do
  not re-introduce it under a different name — the self-only check
  supersedes it.
- The `body.team` override check is retained but renamed:
  `cross_team_dispatch_forbidden` → `thread_dispatch_team_override_forbidden`
  / `talk_dispatch_team_override_forbidden`. Still reachable because
  `body.team` is independent of `body.target_agent` — a self-dispatching
  caller can still send a foreign team and get rejected.
- Error codes were renamed: `worker_must_self_dispatch` →
  `thread_dispatch_must_be_self` / `talk_dispatch_must_be_self`.
- Grandfathered tasks (rows with `dispatched_from_thread_id` predating
  2026-05-28 that target a different agent) continue to function: the
  followup hook still fires on their terminals. The route guard only gates
  new dispatch calls.
- The `task_dispatched` audit row's `dispatcher_role` field still records
  the dispatcher's actual role at dispatch time (manager vs worker) —
  under the new rule that role describes both dispatcher and target, since
  they are now always the same agent.
- The shared hint string lives at `src/daemon/routes/_doctrine.py`
  (`SELF_DISPATCH_HINT`). Both routes import it; keep wording in sync.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): document thread/talk self-only dispatch invariants"
```

---

## Task 9: Run the full test suite

- [ ] **Step 1: Run unit tests**

Run: `uv run pytest tests/ -v`

Expected: all tests pass. No regressions in unrelated test files (audit, KB, jobs, etc.).

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/ -v -m integration`

Expected: all pass, including the new `test_thread_self_dispatch_phase_e2e.py`.

- [ ] **Step 3: Run the OpenAPI snapshot test**

Run: `uv run pytest tests/contract/test_openapi_snapshot.py -v`

Expected: PASS — no route or schema change in this plan, so the snapshot stays current. If it fails, something in the change inadvertently shifted the OpenAPI document; investigate before regenerating.

- [ ] **Step 4: Run the web TS coverage test**

Run: `cd web && npm test -- openapi-coverage.test.ts && cd ..`

Expected: PASS for the same reason.

- [ ] **Step 5: Commit nothing here** — this is a verification gate. If any step fails, return to the relevant earlier task to fix.

---

## Task 10: Founder action note (post-merge)

Not implementable by an agent — listed so the executor surfaces it back to the founder:

After the PR merges, the founder should run (from any orgs that have managers who might encounter the new rule):

```bash
happyranch kb add \
  --slug thread-and-talk-dispatch-doctrine \
  --type doctrine \
  --topic coordination-vs-iteration \
  --source-task TASK-547 \
  --title "Thread / talk dispatch is self-only" \
  --body "$(cat docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md | sed -n '/^## 9\./,/^## 10\./p')"
```

This surfaces the founder ruling to every future agent invocation via the bootstrap KB context block.

- [ ] **Surface this note to the founder when execution completes; do not run the `happyranch kb add` command yourself.**
