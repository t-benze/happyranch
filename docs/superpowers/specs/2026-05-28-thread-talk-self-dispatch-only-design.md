# Thread / Talk Self-Dispatch-Only Rule — Design Spec

**Date:** 2026-05-28
**Status:** Draft, pending implementation.
**Origin:** Founder-reported pattern on THR-010 (2026-05-28): `engineering_head` (manager) used the thread itself as a phase-management board, thread-dispatching TASK-546 (PM, worker) and TASK-547 (senior_dev, worker) as separate root tasks; when TASK-547's design-review came back REQUEST_CHANGES, the `TASK_FOLLOWUP` turn could not dispatch the PM-revision (per `2026-05-28-thread-task-followup-design.md` §6.4), and the thread stalled with a dangling commitment EH could not fulfil. Founder diagnosis: the orchestrator already provides iterative manager-worker loops inside a task tree; threads should be reserved for **founder-visible coordination + cross-team handoffs**, not for in-thread phase management.
**Relates to:**
- `docs/superpowers/specs/2026-05-13-threads-design.md` — the threads primitive this constrains.
- `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md` — the no-dispatch-from-followup rule whose ergonomics motivated this spec; remains unchanged.
- `protocol/skills/thread/SKILL.md` and `protocol/skills/talk/SKILL.md` — the agent-facing skills that gain the doctrine note + redirect on rejection.

## 1. Goal

Constrain `/threads/{id}/dispatch` and `/talks/{id}/dispatch` so that **the dispatcher must equal the target**: an agent may only dispatch a task to itself from inside a thread or talk. Cross-agent work flows through `threads.compose` (messaging) instead.

This forces the doctrine "threads = coordination, task trees = iteration" structurally rather than by convention.

## 2. Motivation

THR-010 evidence (tourism-org, 2026-05-28):

- Seq 1 (founder, send): launches the V1 web-app feature-completion phase.
- Seq 4 (engineering_head, **thread_dispatch → TASK-546**, target=product_manager): PM writes plan.
- Seq 9 (engineering_head, **thread_dispatch → TASK-547**, target=senior_dev): design-review.
- TASK-547 completes REQUEST_CHANGES; the auto-minted `TASK_FOLLOWUP` invocation gives EH a turn to *report*, but per `2026-05-28-thread-task-followup-design.md` §6.4 it cannot dispatch — so EH ends seq 12 with *"I'll dispatch the PM revision out-of-thread next"* and goes dormant. There is no "next" until the founder sends a new message.

Root cause (verified at `src/daemon/routes/threads.py:857-876`):

- Workers are already restricted to self-dispatch from threads: the `worker_must_self_dispatch` rule has shipped since the original threads design (`2026-05-13-threads-design.md`).
- **Managers are exempted.** A manager can thread-dispatch to any worker in their team, or to the team-manager itself.
- This exemption is what let EH treat the thread as a phase board. Removing it forces the same self-only discipline on managers that workers already have.

The same exemption + same shape exists at `src/daemon/routes/talks.py:315-338`.

**The system-design observation:** the existing kernel already supports the right pattern (a manager self-dispatches a phase root → manager-decision loop inside the task tree handles iterative delegation → single terminal fires one `TASK_FOLLOWUP` back to the thread). The exemption was never load-bearing; it just made the wrong pattern available.

## 3. Non-goals

**Out of scope for v1:**

- Backfilling: existing cross-agent dispatches (e.g., TASK-546, TASK-547 on THR-010) are **grandfathered**. The rule applies to new dispatch calls only. Their `TASK_FOLLOWUP` turns continue to land in the originating thread per the existing followup spec.
- Changing `TASK_FOLLOWUP` dispatch policy. `2026-05-28-thread-task-followup-design.md` §6.4 stands: followup turns still cannot dispatch.
- Changing `threads.compose` semantics. Cross-agent / cross-team messaging continues to mint REPLY/BOOTSTRAP turns for the addressed participants exactly as today.
- Top-level `grassland dispatch` (founder CLI / Feishu DISPATCH). The founder retains full dispatch authority from outside thread/talk context.
- Observability layer (counting dispatches per thread for dashboard). Founder dashboard is unshipped; revisit when it lands.

## 4. The rule

For both `/threads/{id}/dispatch` and `/talks/{id}/dispatch`:

```
if effective_target != dispatcher:
    raise 403 {
        "code":            "<route>_dispatch_must_be_self",
        "dispatcher":      dispatcher,
        "requested_target": effective_target,
        "hint":             "<see §6>"
    }
```

`effective_target` is computed as today: `body.target_agent if body.target_agent is not None else dispatcher`. So an agent that calls dispatch with **no** `target_agent` continues to self-dispatch by default; an agent that explicitly sets `target_agent=<themselves>` succeeds; an agent that sets it to any other identity is rejected.

The pre-existing `worker_must_self_dispatch` error code is **renamed** to the unified `thread_dispatch_must_be_self` / `talk_dispatch_must_be_self` — see §7.

## 5. What the rule replaces

The new rule **subsumes and simplifies** two existing checks in each route:

| Existing check | Status under new rule |
|---|---|
| `worker_must_self_dispatch` (workers) | Folded into the unified self-only rule (renamed). |
| `target_not_in_team` (managers dispatching outside their team) | **Unreachable** when `target == dispatcher` (the dispatcher is in their own team by definition). Removed as dead code. |
| `cross_team_dispatch_forbidden` (body.team override mismatch) | **Still reachable**, but renamed to `thread_dispatch_team_override_forbidden` / `talk_dispatch_team_override_forbidden`. `body.team` is independent of `body.target_agent`: a self-dispatching caller can still send `body.team=<other-team>` and the request is rejected. Retained as defense-in-depth; the only useful values for `body.team` under the new rule are `dispatcher_team` or unset. |
| `dispatcher_team_unknown` | Still required — guards against the agent file existing without team membership. Retained. |

After the change, the body's `target_agent` and `team` fields stay accepted (backward-compatible) but their only valid values are `dispatcher` and `dispatcher_team` respectively. The route's behavior collapses to: dispatcher → root task assigned to dispatcher, in dispatcher's team, with `dispatched_from_thread_id` (or `_talk_id`) set. Sending any other `target_agent` produces `thread_dispatch_must_be_self`; sending any other `team` produces `thread_dispatch_team_override_forbidden`.

## 6. Error hint (verbatim)

Embedded in the 403 envelope's `hint` field so the agent reading the rejection learns the doctrine immediately:

```
Threads (and talks) only accept self-dispatch.

For cross-agent work, either:
  (a) self-dispatch a manager root and delegate internally via the
      manager-decision loop (recommended for iterative phase work), or
  (b) use `grassland threads compose --to <other-agent>` to address
      the other agent (or their team's manager) as a thread message,
      and let them drive their own work.

Cross-team handoffs always route through compose, not dispatch.
```

The skill files (`protocol/skills/thread/SKILL.md`, `protocol/skills/talk/SKILL.md`) get the same doctrine in a positive framing — see §10.

## 7. Implementation

### 7.1 Route guards

**`src/daemon/routes/threads.py:857-876`** — replace the manager-vs-worker branching block with a single self-only check:

```python
# (inside the existing teams_lock-held block)
effective_target = body.target_agent if body.target_agent is not None else dispatcher
if effective_target != dispatcher:
    raise HTTPException(
        status_code=403,
        detail={
            "code": "thread_dispatch_must_be_self",
            "dispatcher": dispatcher,
            "requested_target": effective_target,
            "hint": _SELF_DISPATCH_HINT,  # §6
        },
    )
# (team-membership branches removed — target == dispatcher by construction)
```

`effective_team` simplifies similarly:

```python
effective_team = body.team if body.team is not None else dispatcher_team
if effective_team != dispatcher_team:
    raise HTTPException(
        status_code=403,
        detail={
            "code": "thread_dispatch_team_override_forbidden",
            "dispatcher_team": dispatcher_team,
            "requested_team": effective_team,
            "hint": _SELF_DISPATCH_HINT,
        },
    )
```

(A manager calling `body.team=<other-team>` was already forbidden under the old rule via `cross_team_dispatch_forbidden`; this rename is for clarity now that "cross-team" is no longer the only blocked case.)

The `is_manager` boolean is no longer needed for the check; drop the local. The `dispatcher_role` field still written to the `task_dispatched` audit row is computed from `org.teams.is_team_manager(dispatcher)` at audit time (unchanged behavior).

**`src/daemon/routes/talks.py:315-338`** — mirror the same simplification.

### 7.2 Hint constant

Add to a shared module (`src/daemon/routes/_doctrine.py` — new file, ~10 lines) so threads and talks reuse the same wording:

```python
SELF_DISPATCH_HINT = """\
Threads (and talks) only accept self-dispatch.

For cross-agent work, either:
  (a) self-dispatch a manager root and delegate internally via the
      manager-decision loop (recommended for iterative phase work), or
  (b) use `grassland threads compose --to <other-agent>` to address
      the other agent (or their team's manager) as a thread message,
      and let them drive their own work.

Cross-team handoffs always route through compose, not dispatch.
"""
```

Both routes import and use this constant.

### 7.3 No data-model change

`dispatched_from_thread_id`, `dispatched_from_talk_id`, the `task_dispatched` audit row, and `record_dispatch_on_invocation` are all unchanged. The `task_dispatched` audit payload still carries `dispatcher_role` — under the new rule the recorded `effective_target == dispatcher` always, but `dispatcher_role` continues to reflect the dispatcher's actual role (`"manager"` if `is_team_manager(dispatcher)` else `"worker"`).

## 8. Grandfathering existing threads and talks

The change is **point-in-time**: the route validation fires on each new dispatch call. Existing cross-agent dispatches that already minted a task (TASK-546, TASK-547 on THR-010, plus any equivalent rows in other orgs) remain valid:

- Their `tasks.dispatched_from_thread_id` rows persist untouched.
- The followup hook (`_maybe_post_thread_followup`) continues to fire on their terminal transitions, posting `task_completed` / `task_failed` system messages and minting `TASK_FOLLOWUP` invocations for the original dispatcher.
- The TASK_FOLLOWUP turn still cannot dispatch (per the existing followup spec), so any further work on those phases has to go through a fresh founder turn — same as today. THR-010 is the canonical example: the founder will need to either send a new message ("go ahead with PM revision") or, ideally, redirect EH to self-dispatch a phase root from a new turn.

No migration script. No backfill. No retroactive cleanup.

## 9. Doctrine: cross-team handoff pattern

Spelled out here because it's the user-visible "what do I do instead?" answer:

| Want to… | Old way (now rejected) | New way |
|---|---|---|
| Kick off iterative phase work in your own team | `threads dispatch --to <worker-in-team>` | `threads dispatch` (defaults to self) → self-managed root → delegate internally |
| Loop another agent in your team into a thread for back-and-forth | `threads dispatch --to <other-agent>` | `threads compose --to <other-agent>` (or `threads invite`) — messages, not tasks |
| Hand a task to a different team | `threads dispatch --to <other-team-agent>` (was already blocked) | `threads compose --to <other-team-manager>` — they receive BOOTSTRAP, decide whether to self-dispatch |
| Founder asks an agent to push work to a third party | Founder message in thread → addressed agent thread-dispatches the third party | Founder message in thread → addressed agent self-dispatches a phase root, or composes a new thread addressing the third party |

The pattern: **dispatch is for "I will work on this"; compose is for "you, please consider working on this."**

## 10. Skill updates

**`protocol/skills/thread/SKILL.md`** — add a section under existing dispatch guidance:

```markdown
## Dispatch from a thread is self-only

When you are participating in a thread (REPLY / BOOTSTRAP turn), `grassland
threads dispatch` may only target **yourself**. The runtime rejects any other
target with `thread_dispatch_must_be_self`.

This is intentional. Threads exist for founder-visible coordination and
cross-team handoffs. Iterative work (review → revise → re-review, fan-out to
multiple sub-tasks) belongs inside a task tree, where the manager-decision
loop handles delegation natively.

### Patterns

- **Phase work in your own team:** self-dispatch a root task with a phase
  brief. If you are a manager, your manager-decision loop drives delegation
  to workers internally. The thread sees one `task_completed` / `task_failed`
  system message and one TASK_FOLLOWUP turn at the end.

- **Loop in another agent in your team:** use `grassland threads compose --to
  <agent>` or `grassland threads invite`. They receive a thread invocation
  (BOOTSTRAP or REPLY) and decide what to do with it.

- **Cross-team handoff:** use `grassland threads compose --to <other-team-
  manager>` — possibly opening a new thread for the cross-team subject.
  Their manager receives a BOOTSTRAP turn and self-dispatches if they take
  the work on.

If you see `thread_dispatch_must_be_self` (or `talk_dispatch_must_be_self`)
in an error: you tried to push work onto another agent from inside a thread
or talk. Re-route via compose, or self-dispatch and own the phase.
```

**`protocol/skills/talk/SKILL.md`** — same doctrine, adapted to the 1:1 talk surface (compose is replaced by "end the talk and open a thread / send a new compose").

**`protocol/skills/dispatch/SKILL.md`** — single sentence at the top reminding readers that the same skill in thread/talk context is self-only.

## 11. CLAUDE.md update

Add a subsection under the existing threads notes (around the current "Thread task-followup" entry) documenting the non-obvious invariant:

```markdown
## Thread / talk dispatch self-only rule

Both `/threads/{id}/dispatch` and `/talks/{id}/dispatch` reject any call where
`effective_target != dispatcher`. The doctrine is "threads/talks are
coordination surfaces; iterative work lives in task trees." See spec
`docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

**Non-obvious invariants:**

- The rule applies uniformly to managers AND workers. Pre-2026-05-28 history:
  workers were already restricted (`worker_must_self_dispatch`); managers
  were exempted. THR-010 surfaced the exemption as a footgun. The new code
  collapses both paths into a single check.
- `target_not_in_team` (manager branch) is unreachable under the new rule
  and has been removed from `routes/threads.py` and `routes/talks.py`. Do
  not re-introduce it — the self-only check supersedes it.
- The `body.team` override check is retained but renamed:
  `cross_team_dispatch_forbidden` → `thread_dispatch_team_override_forbidden`
  / `talk_dispatch_team_override_forbidden`. It is still reachable because
  `body.team` is independent of `body.target_agent` — a self-dispatching
  caller can still send a foreign team and get rejected.
- Grandfathered tasks (rows with `dispatched_from_thread_id` predating
  2026-05-28 that target a different agent) continue to function: the
  followup hook still fires on their terminals. The route guard only
  gates new dispatch calls.
- The `task_dispatched` audit row's `dispatcher_role` field still records
  the dispatcher's actual role at dispatch time (manager vs worker) — under
  the new rule that role describes both dispatcher and target, since they
  are now always the same agent.
```

## 12. Doctrine injection into the bootstrap doc

**Supersedes the originally-planned founder KB entry.** This is a structural,
system-wide rule that the runtime enforces uniformly across every org — the
right surface is the bootstrap doc that every agent reads on every session,
not a per-org KB entry that has to be added by hand once per org.

New section helper `_thread_talk_dispatch_doctrine_section()` in
`src/orchestrator/workspace_adapters.py` emits an H2 block titled
**"Thread and Talk Dispatch are Self-Only"**. The block names both rejection
codes (`thread_dispatch_must_be_self`, `talk_dispatch_must_be_self`),
explains the doctrine in plain English, and points at the recommended
alternatives (self-dispatch a manager root; use `grassland threads compose`
for cross-agent work). The section is wired into `_build_sections` between
the Shared Assets and Long-running-commands blocks — both are operational
guardrails about how to interact with system surfaces.

The section title is registered in `_RESERVED_AGENT_BODY_HEADERS` so an
agent's `.md` body cannot author a colliding section (the assembled
bootstrap would otherwise carry two same-titled blocks).

The injection runs uniformly across all three executors: Claude (`CLAUDE.md`),
Codex (`AGENTS.md`), opencode (`AGENTS.md`) — `_build_sections` is shared
across all three adapters.

Word-choice note: the section deliberately avoids the contract-keywords
"delegate" and "escalate" in prose. Those terms are reserved for the
NextStep decision schema and are forbidden from the bootstrap doc by
`test_codex_agents_md_does_not_inline_completion_contract`. Use "spawn
sub-tasks" or "drive internal work" instead.

No founder action is required post-merge. The doctrine ships uniformly
with the next workspace rewrite (which `grassland init-agent` triggers
on every session bootstrap).

## 13. Test plan

### Unit (`tests/daemon/test_threads_routes.py`, `tests/daemon/test_talks_dispatch.py`)

Rename / extend existing self-dispatch tests:

- `test_thread_dispatch_worker_to_self_succeeds` — unchanged (passes today).
- `test_thread_dispatch_worker_to_other_rejected_self_only` — renames `worker_must_self_dispatch` → `thread_dispatch_must_be_self` in the assertion.
- `test_thread_dispatch_manager_to_other_in_team_rejected` — NEW. Manager tries to dispatch to a worker in their own team; expect 403 `thread_dispatch_must_be_self`. (This is the THR-010 case.)
- `test_thread_dispatch_manager_to_self_succeeds` — NEW. Manager dispatches with no `target_agent` (or `target_agent=<themselves>`); expect 200.
- `test_thread_dispatch_team_override_rejected` — body.team set to a foreign team; expect 403 `thread_dispatch_team_override_forbidden` (was `cross_team_dispatch_forbidden`).
- Drop `test_manager_target_not_in_team` from talks (becomes unreachable; the self-only path covers it).

Mirror the same suite under `tests/daemon/test_talks_dispatch.py` with `talk_dispatch_must_be_self` / `talk_dispatch_team_override_forbidden`.

### Integration (`tests/integration/`)

One new e2e test under `tests/integration/test_thread_self_dispatch_phase_e2e.py`:

- Founder composes thread → manager replies → manager self-dispatches a phase root → manager-as-task delegates to a worker in their team → worker completes → manager-task continues, dispatches another delegate → manager-task completes → single `TASK_FOLLOWUP` invocation lands in the thread with the phase summary.
- Asserts: thread transcript has exactly one `task_dispatched` SYSTEM message (the manager's self-dispatch), one `task_completed` SYSTEM message at the phase root's terminal, and one manager `reply` (the followup). The delegated sub-tasks are NOT visible in the thread.

Reuses `fake_claude_plan_env` (multi-step manager-decision loop) and `fake_claude_thread_plan_env` (followup turn) per the dual-plan pattern.

### OpenAPI snapshot

No schema or path changes. `tests/contract/test_openapi_snapshot.py` should pass unchanged. The error-code strings appear only in 403 detail envelopes (free-form JSON), not in the OpenAPI document.

## 14. Migration / rollout

- No DB schema migration.
- No backfill (§8 grandfathering).
- Skill files are read by agents at workspace bootstrap; existing workspaces continue to read updated skill text on each session (skills are loaded from the runtime, not baked into the workspace).
- KB entry surfaces the doctrine to every new agent invocation via the bootstrap context builder.
- Rollback: revert the route guards. Grandfathered tasks remain valid; the only behavioral change on rollback is that managers can again target other agents.

## 15. Open questions

None blocking. Two flagged for future:

- **Audit signal for repeat-dispatch attempts.** A bored signal: under the new rule, the same agent calling dispatch with `target != dispatcher` is now an immediate 403, no historical state needed. But a "manager attempted N times across M threads" counter could feed the founder dashboard once shipped. Not v1.
- **Talks-as-dispatch surface.** The talk-dispatch route is rarely used in practice (most talks are conversational, not task-spawning). If usage stays low post-rule, consider deprecating talk-dispatch entirely in favor of "end the talk → use `grassland dispatch`." Out of scope for this spec.

## 16. Implementation order

1. New shared module `src/daemon/routes/_doctrine.py` with `SELF_DISPATCH_HINT`.
2. `src/daemon/routes/threads.py:857-876` — collapse to self-only check; rename error code; remove dead `target_not_in_team` branch.
3. `src/daemon/routes/talks.py:315-338` — same change.
4. Update `tests/daemon/test_threads_routes.py` and `tests/daemon/test_talks_dispatch.py` per §13 unit set.
5. New integration test `tests/integration/test_thread_self_dispatch_phase_e2e.py`.
6. `protocol/skills/thread/SKILL.md`, `protocol/skills/talk/SKILL.md`, `protocol/skills/dispatch/SKILL.md` — doctrine sections.
7. `CLAUDE.md` — invariants subsection.
8. `src/orchestrator/workspace_adapters.py` — `_thread_talk_dispatch_doctrine_section()` + wire into `_build_sections` + add header to `_RESERVED_AGENT_BODY_HEADERS`. Tests covering all three executors.
9. `web/src/features/talks/strings.ts` + `DispatchFromTalkDialog.tsx` — map the new error codes; drop the now-vestigial `target_agent` and `team` inputs from the dispatch dialog.
