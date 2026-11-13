# Idempotent Manager Completion Callback — Design Spike

**Status:** DESIGN-ONLY. Founder sign-off gated. No runtime code, no `protocol/`
edits, no schema changes are made by this spec. It specifies the change; a
later BUILD phase implements it under maker-checker.

**Task:** TASK-3127 (THR-110)
**Date:** 2026-07-21
**Author:** engineering_manager
**Related:** MEM-365 (false-orphan quantification), MEM-233 (the trap),
THR-090 Track A (orphaned-result consume), THR-079 (pid-liveness), THR-090
Track B (zombie reaper).

---

## 1. Problem

`POST /tasks/{task_id}/completion` is **idempotent-hostile**. On the success
path it clears the in-memory `SessionTracker`
(`runtime/daemon/routes/tasks.py:460`, `org.sessions.clear(task_id, body.agent)`).
A retry of the same callback after a *lost or torn-down HTTP response*
(turn-end teardown, a transient socket blip) then re-enters `submit_completion`,
finds the tracker empty, and raises **`409 unknown_session`**
(`tasks.py:393-397`).

The agent reads that 409 as *"my callback never landed"* and writes a stale
`"orphaned, re-report on revisit"` note into its memory — even though the
result already persisted to `task_results`, the orchestration step already
consumed the decision, and the task already advanced. The revisit never comes
(the task is done); the false note poisons the next session. This is the
MEM-233 trap.

### 1.1 Evidence (from THR-110 DB autopsy, MEM-365)

- `daemon_restart_failure` peaked 65 on 2026-07-01, decayed to ~zero; the
  **last** one was 2026-07-13. **Zero** across ~60 EM roots since 2026-07-14.
- For every completed/escalated EM task in that window,
  `session_start` count == `completion_report` count (audit_log). Every
  started session landed exactly one report. TASK-3019's note claimed "no
  completion_report, unknown_session on retry" — the DB shows `creps=4`. False.
- Conclusion: the "callback ORPHANED (daemon flap)" epidemic in memory is a
  **false-perception class**, not a real orphan wave. The real orphan wave
  (2026-07-01 redeploy churn) has subsided, helped by THR-090 Track A (#398)
  and THR-079 (#357) making mid-restart callbacks recoverable.

The remaining defect is purely the callback route's hostility to a
duplicate POST of a call that **already succeeded**.

---

## 2. STEP-0 Reconciliation (Confusion Protocol)

The brief's line numbers were reconciled against live `main` @ `82b10e0c`.
Findings:

| Brief claim | Live main @ 82b10e0c | Status |
|---|---|---|
| tracker cleared at `tasks.py:460` | `tasks.py:460` `org.sessions.clear(...)` | ✅ exact |
| `unknown_session` raised at `tasks.py:389-397` | gate at `389-397`; `if expected is None` at `393-397` | ✅ exact |
| completion seam `tasks.py:385-460` | `submit_completion` spans `385-472` | ✅ |
| `/progress` gate at `tasks.py:494-504` | `submit_progress` gate at `494-504` | ✅ exact |
| `current_session_id` persisted at `orchestrator.py:654` | `runtime/orchestrator/orchestrator.py:658` (`update_task(task_id, executor_pid=pid, current_session_id=session_id)`) | ⚠️ path+line drift — noted below |
| startup-sweep consume in `__main__.py` | `runtime/daemon/__main__.py` `_sweep_on_startup`, Branch-1 consume at `~136-172` | ✅ (path is `runtime/daemon/__main__.py`) |
| zombie-reaper fingerprint-consume | `runtime/daemon/zombie_reaper.py:138-160, 256-257` | ✅ |

**Path drift:** the orchestrator lives at `runtime/orchestrator/orchestrator.py`
(not `runtime/application/orchestrator.py`); the `current_session_id` +
`executor_pid` persist is line **658**, not 654. Everything else is exact.

### 2.1 STOP-and-surface: the idempotency key is NOT schema-unique

The brief asked to stop and surface **if `(task_id, agent, session_id)` is not
actually unique in `task_results`**. It is **not** unique at the schema level:

```sql
-- runtime/infrastructure/database.py:485
CREATE TABLE IF NOT EXISTS task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- only key
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ...
);
```

There is **no `UNIQUE` constraint** on `(task_id, agent, session_id)` and no
index enforcing it. The existing accessor that queries on exactly this triple
already assumes non-uniqueness — it disambiguates with `ORDER BY id DESC LIMIT 1`:

```python
# runtime/infrastructure/database.py:2502
def get_latest_task_result(self, task_id, agent, session_id) -> dict | None:
    ... "SELECT * FROM task_results WHERE task_id = ? AND agent = ? AND session_id = ? "
        "ORDER BY id DESC LIMIT 1"
```

**Why this is not a blocker for the design (and why we must NOT "fix" it):**

- Today at most **one** row exists per `(task_id, agent, session_id)` on the
  happy path, precisely because the `tracker.clear()` at `:460` makes a retry
  hit `unknown_session` *before* it can insert a second row. The non-uniqueness
  is latent, not active.
- The primary design (§4) **only reads** this row and returns 200 early — it
  **never inserts** a second row, so it cannot create a duplicate and does not
  depend on uniqueness for correctness. The `LIMIT 1` accessor is exactly the
  right shape.
- Adding a `UNIQUE` constraint would be a **founder-gated schema migration**
  that could fail on any historical duplicate rows and is out of scope for the
  documented false-orphan class. **Do not add it.** Reuse the existing accessor.

This is surfaced, not silently assumed. It shapes the design (read-only
short-circuit) rather than blocking it.

---

## 3. Load-bearing context: three existing consume paths

`get_latest_task_result(task_id, agent, current_session_id)` is **already** the
canonical "did a completion land for this exact session?" probe, used by two
recovery paths today:

1. **Boot sweep, Branch 1 (THR-090 Track A)** —
   `runtime/daemon/__main__.py:136-172`. For an `in_progress` + `block_kind IS
   NULL` task whose `executor_pid` is dead, it reads the orphaned row for
   `current_session_id`, and if present, honors it via
   `_consume_completion_report` (logging the completion to audit first). Guard:
   "err toward a MISS (fail-closed), NEVER replay an already-consumed decision."
2. **Ongoing zombie reaper (THR-090 Track B)** —
   `runtime/daemon/zombie_reaper.py:138-160, 256-257`. Same probe, same
   `_consume_completion_report` tail, for mid-flight discoveries.
3. **Inline happy path** — `run_step_impl` → `_consume_completion_report`
   (`runtime/orchestrator/run_step.py:311, 314`).

All three funnel through `_consume_completion_report`
(`run_step.py:314`), whose four decision branches (`_complete`, `_fail`,
delegate, block) each guard on **`_is_already_terminal(orch, task_id)`**
(`run_step.py:1298`):

```python
def _is_already_terminal(orch, task_id) -> bool:
    existing = orch._db.get_task(task_id)
    return (existing is None
            or existing.status in TERMINAL_STATES
            or existing.cancelled_at is not None)
```

**This terminal-status predicate is the system's load-bearing idempotence
key for decision *application*.** Once a decision has transitioned the task
(COMPLETED / FAILED, or spawned children and re-parked), a second consume of
the same row is a no-op. This is what makes the three consume paths safe to
overlap, and it is what makes option (b) (§5) safe if adopted.

---

## 4. Primary design — Option (a): idempotent 200 on duplicate same-session POST

**Change site:** `submit_completion`, the `expected is None` branch,
`runtime/daemon/routes/tasks.py:393-397`.

### 4.1 Before

```python
# tasks.py:388-397 (current)
_require_task_active(task_id, org.db.get_task(task_id))   # Guard A
expected = org.sessions.get_active(task_id, body.agent)
if expected is None:
    raise HTTPException(409, {"code": "unknown_session", ...})   # <-- false-orphan trigger
if expected != body.session_id:
    raise HTTPException(409, {"code": "session_mismatch", ...})
```

### 4.2 After

```python
_require_task_active(task_id, org.db.get_task(task_id))   # Guard A (UNCHANGED)
expected = org.sessions.get_active(task_id, body.agent)
if expected is None:
    # Idempotency short-circuit (TASK-3127): the tracker is empty because a
    # PRIOR successful POST for this session already cleared it (tasks.py:460).
    # If a task_result row exists for the EXACT (task_id, agent, session_id),
    # this is a duplicate of a call that already succeeded — return 200
    # "already recorded" instead of a misleading 409 unknown_session. Do NOT
    # insert a second row; do NOT re-run decision side effects.
    prior = org.db.get_latest_task_result(task_id, body.agent, body.session_id)
    if prior is not None:
        return {"ok": True, "idempotent": True}   # already recorded
    # No persisted row for this session -> genuinely-unknown / fabricated
    # session. Preserve the security gate: STILL 409.
    raise HTTPException(409, {"code": "unknown_session", ...})
if expected != body.session_id:
    raise HTTPException(409, {"code": "session_mismatch", ...})
```

### 4.3 Control-flow properties

- **Placement is deliberately *inside* the `expected is None` branch**, after
  `_require_task_active`. It narrows the short-circuit to exactly the
  documented false-orphan trigger (`unknown_session`) and touches nothing else.
- **No new insert.** The retry does not add a `task_results` row (the
  non-uniqueness of §2.1 is therefore irrelevant to correctness).
- **No re-run of side effects.** The decision was consumed by the original
  step (or will be by a recovery path); this route never re-invokes the
  orchestration step. It only answers the HTTP retry.
- **Return shape:** `{"ok": True, "idempotent": True}`. The existing success
  return is `{"ok": True}` (`tasks.py:472`); adding the additive `idempotent`
  flag is backward-compatible (the agent-side reader only checks HTTP 200).
  Whether to include the flag at all is a sign-off item (§9).

### 4.4 The residual case the primary design does NOT cover (and why)

`_require_task_active` (Guard A, `tasks.py:388`) runs **before** the session
gate and rejects a **terminal or cancelled** task with `409 task_not_active`.
So there are two distinct false-signal shapes on a duplicate POST:

| Decision already consumed | Task status at retry | Gate hit first | Error returned |
|---|---|---|---|
| `delegate` / `block` (task stays `in_progress`), **or** the race window before the step consumes | `in_progress` | session gate | `409 unknown_session` → **fixed by option (a)** |
| `done` / `escalate` (task now terminal) | `COMPLETED` / `FAILED` | Guard A | `409 task_not_active` → **NOT reached by option (a)** |

**Recommendation: leave the `task_not_active` case as-is.** Rationale:

- MEM-365 documents `unknown_session` specifically as the false-orphan
  trigger. `task_not_active` is a *truthful* signal — the task genuinely is
  terminal and the agent's decision genuinely was applied.
- Short-circuiting `task_not_active` to 200 would require moving the
  idempotency check *ahead of* `_require_task_active`, which is the exact
  ordering the cancel-race design
  (`docs/superpowers/specs/2026-05-26-cancel-race-design.md` §5.1) deliberately
  established: a **cancelled** task must reject callbacks so the agent learns
  its session was terminated. Returning 200 "already recorded" for a cancelled
  task would mislead the agent into thinking its work landed when cancel won.
- The correct closure for the `task_not_active` case is the **agent-behavior
  change already mandated by MEM-365**: stop reflexively recording "orphaned";
  verify against the DB before writing an orphan note. The route fix (option a)
  removes the *specific* 409 that the DB check can't easily disambiguate for a
  still-active task; the terminal case is already DB-verifiable by the agent.

Whether to *also* idempotent-ize `task_not_active` is surfaced as a discrete
sign-off sub-item (§9, item 5). EM recommendation: **defer** (keep the
cancel-race ordering intact).

---

## 5. Option (b) — tracker-miss fallback to `tasks.current_session_id`

**Idea (from the brief):** on a tracker miss where **no** `task_result` row
exists, fall back to the persisted `tasks.current_session_id`
(`orchestrator.py:658`). Accept the callback iff
`db.current_session_id == body.session_id` **AND** the task is non-terminal —
recovering a **true restart-orphan** (an in-flight callback whose session's
tracker entry was wiped by a daemon restart before the result ever persisted).

### 5.1 Interaction with the existing recovery paths (the double-process risk)

The concern: if the `/completion` route accepts-and-consumes a true-orphan via
`current_session_id`, and the **boot sweep** / **zombie reaper** *also* consume
the same `task_result` on the next restart/loop, the decision is applied twice.

**Analysis — it is already structurally safe, for two reasons:**

1. **Same terminal-status idempotence key.** Any accept-path for option (b)
   would (like the happy path) insert the row and consume it through
   `_consume_completion_report`, whose branches guard on
   `_is_already_terminal` (§3). A second consume by the sweep/reaper is a
   no-op once the task is terminal or re-parked. This is the identical guard
   THR-090 relies on for Track A vs Track B overlap today.
2. **Fingerprint self-clears.** The sweep/reaper only act when they *find* an
   unconsumed row for `current_session_id` **and** the task is still a zombie /
   dead-pid. Once option (b) consumes and transitions the task, the reaper's
   next sweep sees a non-zombie (terminal/advanced) task and does nothing;
   `zombie_flagged_at` is cleared and a `zombie_cleared` row emitted
   (`protocol/05c` §Ongoing zombie reaper).

So option (b) does **not** introduce a new double-process path — it rides the
same idempotence invariant.

### 5.2 Why option (b) is nonetheless recommended for DEFERRAL

- **It widens the security acceptance surface.** Option (a) only ever
  short-circuits when a persisted result **already exists** for the exact
  session — it accepts nothing new. Option (b) would accept a **brand-new**
  callback on the strength of `current_session_id` alone (no persisted result
  yet). That is a larger trust surface (the session gate is founder-gated —
  §6) for a much narrower payoff.
- **The payoff is already covered.** A true restart-orphan (tracker wiped, no
  persisted result) is exactly what the **boot sweep Branch 1** and the
  **ongoing zombie reaper** already recover — the sweep consumes it on the next
  restart, the reaper within one TTL (150s) mid-flight. Option (b)'s only
  marginal gain is recovering such an orphan **without waiting** for the next
  reaper tick, in the narrow window where the agent's own retry arrives first.
- **The real orphan wave has subsided** (§1.1): last `daemon_restart_failure`
  2026-07-13; zero since 2026-07-14. There is no current evidence of
  true-orphans that the sweep+reaper miss.

**EM recommendation: adopt (a) now; DEFER (b)** until there is evidence of
same-process true-orphans that the sweep/reaper do not catch. If (b) is later
adopted, it MUST route through the insert → `_consume_completion_report` tail
(reusing the §3 idempotence key) and MUST keep the exact-session-match
condition (`db.current_session_id == body.session_id`) plus the non-terminal
check — never a blanket accept.

---

## 6. Security argument — genuinely-unknown sessions STILL 409

The one invariant that must not regress: a fabricated / never-seen
`session_id` must **still** be rejected with `409 unknown_session`.

- **Option (a):** the short-circuit returns 200 **only** when
  `get_latest_task_result(task_id, body.agent, body.session_id)` returns a
  row — i.e., only when *the daemon itself previously persisted a result under
  that exact session*. A fabricated session_id was never spawned, never
  cleared a tracker, and never inserted a row → `prior is None` → the code
  falls through to the **unchanged** `raise 409 unknown_session`. No blanket
  accept. The `(task_id, agent, session_id)` triple is the authenticator; all
  three must match a persisted row.
- **Option (b), if ever adopted:** acceptance is gated on
  `db.current_session_id == body.session_id` (a value the daemon itself wrote
  at session start, `orchestrator.py:658`) AND task non-terminal. A fabricated
  session still fails the equality and 409s. Still no blanket accept.

The session gate remains **founder-gated** (see §7); this spec proposes no
weakening of it, only a read-only idempotency short-circuit that is strictly
*narrower* than the current accept set (it accepts only exact-match duplicates
of already-succeeded calls).

---

## 7. Load-bearing-invariant impact

| Invariant | Impact |
|---|---|
| **Session gate is founder-gated** (`CLAUDE.md` — auth/permission-model surface) | Touched **read-only**: adds a short-circuit that is *narrower* than today's accept set (only exact-session duplicates that already persisted). No new session is trusted. **Founder sign-off required** (this is why the spec is gated). |
| **Audit-row shapes** | **Unchanged.** No new audit action, no changed `task_id` scope-prefix. Option (a) emits **no** new audit row (the original `completion_report` row already logged on the first, successful call). Optionally the route could emit a `completion_report_duplicate` beat — **not** recommended (adds an audit shape); sign-off item §9. |
| **Schema / columns** | **None.** No new table, no new column, no migration. Reuses `task_results` and `tasks.current_session_id` (both already present). §2.1: do **not** add a UNIQUE constraint. |
| **`task_id`-column overloading** | Untouched. |
| **Maker-checker** | The BUILD phase must go dev_agent → code_reviewer (codex, model-diversity) → qa_engineer. qa scope explicitly includes callback routes / SessionTracker (integration-test trigger). EM does not both write and approve. |
| **Contract surfaces (OpenAPI + web `openapi-coverage.test.ts`)** | The route path and method are unchanged; only the *body* of an existing 409 branch changes to a 200. If the response *schema* is left as-is (`{"ok": true}` with an optional additive field), **no OpenAPI regen is needed**. If the `idempotent` flag is added to the declared response model, regen **both** surfaces in the BUILD PR (MEM-094 / MEM-148). Recommendation: keep the response model untyped/loose as today → no contract drift. Sign-off item §9. |

---

## 8. Doc-parity deltas (SPECIFIED, not applied — `protocol/` is founder-owned)

The BUILD PR must land these deltas **in the same PR** as the code. Exact
proposed text:

### 8.1 `protocol/00-completion-contract.md`

Add, in the section describing the completion callback / session ownership
(near the decision-action list, ~line 62), a new subsection:

> **Idempotent retry semantics.** `POST /tasks/{id}/completion` is safe to
> retry for the *same session*. If a result for the exact
> `(task_id, agent, session_id)` was already recorded, the route returns
> `200 {"ok": true}` ("already recorded") instead of `409 unknown_session`,
> even though the in-memory session tracker was cleared by the first
> successful call. A retry whose `session_id` was **never** persisted still
> receives `409 unknown_session` — the `(task_id, agent, session_id)` triple
> is the authenticator, and all three must match a persisted result. Agents
> MUST treat a `200` (including an idempotent 200) as "landed" and MUST NOT
> record the callback as orphaned. A terminal task still returns
> `409 task_not_active`; verify task status in the DB before treating that as
> a failure.

### 8.2 `protocol/05c-orchestrator.md`

In the "Daemon restart recovery" section (~line 329, "Orphaned task_result
consumption"), append a cross-reference paragraph:

> **Duplicate live callback (TASK-3127).** Independently of restart recovery,
> the `/completion` route itself short-circuits a duplicate POST of an
> already-succeeded call: on a tracker miss it probes
> `get_latest_task_result(task_id, agent, session_id)` and returns an
> idempotent `200` when the row exists (the same probe the boot sweep and
> ongoing reaper use, §Ongoing zombie reaper). This closes the false-orphan
> class where a lost HTTP response drove a duplicate POST into
> `409 unknown_session`. It does **not** add a new transition edge and does
> **not** re-consume the decision — the terminal-status idempotence guard
> (`_is_already_terminal`) remains the single point that applies a decision
> at most once across the inline, boot-sweep, and reaper paths.

*(If option (b) is later adopted, an additional paragraph will describe the
`current_session_id`-fallback accept path and its reliance on the same
`_is_already_terminal` guard; deferred with option (b).)*

---

## 9. `/progress` route — include or not?

`submit_progress` (`tasks.py:494-504`) has the **same** `unknown_session` gate.
But `/progress` **does not clear the tracker** (the agent keeps working after a
beat), so the false-orphan class **cannot arise** for it: a progress
retry-after-lost-response hits a *still-populated* tracker and returns 200
normally. The only ways `/progress` reaches `unknown_session` are a
genuinely-unknown session or a post-completion beat (the latter is caught first
by `_require_task_active`). **EM recommendation: do NOT extend option (a) to
`/progress`** — there is no false-orphan to close there, and adding the probe
would only widen surface for no benefit.

---

## 10. Test plan (for the BUILD phase)

Unit (`tests/` — daemon route tests, no daemon restart needed):

1. **duplicate-POST-same-session → 200.** Spawn a session, POST completion
   (asserts 200, row inserted, tracker cleared), POST the identical body again
   → assert `200` with `ok=True` (idempotent), assert `task_results` still has
   exactly **one** row for that session (no second insert), assert the
   decision side-effect ran exactly once.
2. **fabricated-session → 409.** With an empty/never-populated tracker and no
   persisted row, POST completion with an invented `session_id` → assert
   `409 unknown_session`.
3. **wrong-session, tracker populated → 409 session_mismatch** (regression:
   the existing `expected != session_id` branch is unchanged).
4. **terminal task retry → 409 task_not_active** (regression: Guard A still
   fires first for a done/escalate-consumed task; documents the §4.4 residual).
5. **`/progress` unknown-session → 409** (regression: progress is NOT
   idempotent-ized).

Integration (qa_engineer, callback-route scope):

6. **(option b only, if adopted)** restart-orphan recovery: persist
   `current_session_id`, clear the tracker (simulate restart), POST a
   first-time completion for that session with no prior row → assert accept +
   single consume; then run a boot sweep and assert **no** double-process
   (task already terminal / advanced).

---

## 11. BUILD-phase note (not now)

Before editing `submit_completion`, the dev_agent must run
`gitnexus_impact` on `submit_completion` and report the blast radius
(callback route → SessionTracker → orchestration step). Diff scope must stay
within: `runtime/daemon/routes/tasks.py` (+ the doc-parity deltas §8, + tests).
No `runtime/orchestrator/`, `zombie_reaper.py`, or `__main__.py` edits are
required for option (a) — it reuses `get_latest_task_result` verbatim.

---

## 12. Recommendation summary

- **Adopt option (a)** — the read-only idempotent-200 short-circuit in the
  `expected is None` branch. It closes the documented false-orphan class with
  **no new acceptance surface, no schema change, no audit-shape change**, and
  reuses the existing `get_latest_task_result` accessor.
- **Defer option (b)** — its payoff (true restart-orphan recovery) is already
  covered by the boot sweep + zombie reaper; its cost (a wider founder-gated
  accept surface) is not justified by current evidence. Revisit only if
  same-process true-orphans that the sweep/reaper miss are observed.
- **Exclude `/progress`** — no false-orphan class exists there.
- **Leave `task_not_active` as-is** — truthful signal; agent-side MEM-365
  behavior change is the correct closure; do not disturb the cancel-race
  ordering.

---

## 13. Sign-off gate (discrete founder decisions)

The BUILD phase does not begin until the founder rules on:

1. **Adopt option (a)** (idempotent-200 on duplicate same-session POST in the
   `unknown_session` branch) — EM recommends **YES**.
2. **Option (b)** (tracker-miss → `current_session_id` fallback accept) —
   EM recommends **DEFER**. (Adopt-with-(a) / defer / reject.)
3. **Contract-doc wording** — approve the `protocol/00-completion-contract.md`
   (§8.1) and `protocol/05c-orchestrator.md` (§8.2) delta text, or amend.
4. **`/progress` inclusion** — EM recommends **EXCLUDE**. (include / exclude.)
5. **Also idempotent-ize the `task_not_active` (terminal-task) retry?** —
   EM recommends **NO / defer** (preserve the cancel-race ordering; rely on
   MEM-365 agent-behavior change).
6. **Response shape** — add an additive `{"idempotent": true}` flag to the 200
   body (EM: harmless, optional), and confirm the response model stays
   loose so **no** OpenAPI / `openapi-coverage.test.ts` regen is required
   (EM recommends keep-loose).

Founder sign-off (reply-approval or merge of this spec's PR) is the sole gate.
Merged ≠ materialized: no runtime behavior changes until the BUILD PR ships
under maker-checker.
