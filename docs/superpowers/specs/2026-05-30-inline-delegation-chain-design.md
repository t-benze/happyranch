# Inline Delegation Chain — Design

**Date:** 2026-05-30
**Status:** Design ratified; ready for implementation plan
**Origin:** TASK-577 (engineering_head V1 web-app feature-complete driver) hit the 50-step orchestration cap with ~30 wakes of remaining work because every routine `dev_agent → senior_dev → qa_engineer → merge` gate transition consumed a manager wake. The founder ruled out raising the cap; the chosen lever is to let a manager declare a multi-leg workflow inline at delegation time, so the orchestrator auto-advances routine happy-path legs without a manager wake.

## Goal

Collapse the linear happy-path of a multi-gate workflow into one manager decision. The manager declares "dispatch A, then B if B-verdict matches, then C if C-verdict matches, then wake me." The orchestrator executes the chain, only waking the manager at the final terminal or at any deviation from the declared expectation.

## Non-goals

- **Reusable / named workflows.** No `workflows/<slug>.yaml`, no KB-versioned workflow definitions. Workflows are authored inline in the manager's `decision` payload. (Considered and explicitly cut in brainstorm.)
- **Full DAG with revise loops.** No conditional branching by verdict, no in-chain loops. REVISE iteration stays the manager's job — they handle it on the wake that fires when a chain leg deviates.
- **Worker-side workflow authoring.** Workers do not declare chains. Only team managers speak the `NextStep` protocol; that gate is preserved.
- **Verdict semantics in code.** The system treats verdicts as opaque strings. Per-team vocabulary (APPROVE / REQUEST_CHANGES / PASS / REVISE / BLOCK for engineering; whatever content team picks) lives in each team's workflow KB entry.

## Architecture

### Schema additions

**`src/models.py` — `NextStep` + new `ChainLeg`:**

```python
class ChainLeg(BaseModel):
    agent: str
    prompt: str
    expect_verdict: str | None = None

class NextStep(BaseModel):
    action: Literal["delegate", "done", "escalate"]
    agent: str | None = None
    prompt: str | None = None
    expect_verdict: str | None = None              # NEW — gates the first leg too
    then: list[ChainLeg] = Field(default_factory=list)  # NEW — additional legs
    summary: str | None = None
    reason: str | None = None
```

**`src/models.py` — `CompletionReport`:**

```python
class CompletionReport(BaseModel):
    # ...existing fields...
    verdict: str | None = None  # NEW — optional structured outcome for review/QA-type workers
```

**Database — one new column on `tasks`:**

```sql
ALTER TABLE tasks ADD COLUMN active_chain TEXT NULL;
```

`active_chain` is JSON-serialized when set; NULL means "no chain in flight on this parent." Shape:

```json
{
  "step_index": 0,
  "first_leg_expect_verdict": "APPROVE",
  "legs": [
    {"agent": "senior_dev",  "prompt": "...", "expect_verdict": "APPROVE"},
    {"agent": "qa_engineer", "prompt": "...", "expect_verdict": "PASS"}
  ],
  "step_audit_id": 4521
}
```

- `step_index` points to the currently-in-flight child's position in the chain (0 = first leg in the implicit `decision.agent`+`decision.prompt`; 1..N = entries in `legs`).
- `first_leg_expect_verdict` mirrors `decision.expect_verdict` from the manager's chain-mint payload. Stored here so the chain-advance logic never has to re-read the audit row to route a child terminal.
- `legs` stores only the legs **after** the first; the first leg is the existing `delegate` payload.
- `step_audit_id` is the `audit_log.id` of the `orchestration_step` row that minted the chain — referenced as `chain_origin_step_audit_id` on every `chain_auto_advance` row for audit traceability.

The chain lives on the parent task (the manager task whose step declared the chain). Each spawned child carries `parent_task_id` pointing back at the parent, as today. No new column on children.

### Control flow

**On manager `delegate` with `then` non-empty** (or with `expect_verdict` set, which is also a chain — a 1-leg gated chain):

1. Existing cross-team / role validation runs against **every** leg (first leg + every entry in `then`). Any off-team leg → feedback step back to the manager (existing `feedback` mechanism), no spawn.
2. In the same transaction that today inserts the first child task: persist `active_chain` on the parent with `step_index=0`.
3. Spawn the first leg as a normal child task (existing path unchanged).

**On any child task reaching a terminal state** (in `_enqueue_parent_if_waiting`), before the existing parent-wake:

```
parent = parent_of(child)
chain  = parent.active_chain

if chain is None:
    → existing behavior (wake parent)
    return

if child.status in {FAILED, FAILED_CANCELLED}:
    → clear parent.active_chain
    → wake parent  (existing path)
    return

# child.status == COMPLETED at this point
report = child.completion_report

if report.status == "blocked":
    → clear parent.active_chain
    → wake parent
    return

current_leg_expect = (
    chain.first_leg_expect_verdict if chain.step_index == 0
    else chain.legs[chain.step_index - 1].expect_verdict
)

if current_leg_expect is not None and report.verdict != current_leg_expect:
    → clear parent.active_chain
    → wake parent  (manager handles the mismatch)
    return

next_leg_index = chain.step_index + 1
# Chain.legs has length N (legs 1..N after the first leg at index 0).
# Total legs = N+1. Next leg exists iff next_leg_index <= N.
if next_leg_index > len(chain.legs):
    → clear parent.active_chain
    → wake parent  (final-leg happy-path wake per Q4)
    return

# Auto-advance: spawn next leg
next_leg = chain.legs[next_leg_index - 1]
prompt = next_leg.prompt + _build_prior_leg_context(child, report)
spawn_child_task(
    parent_task_id=parent.id,
    agent=next_leg.agent,
    brief=prompt,
    # inherit per-task overrides (session_timeout_seconds, dispatched_from_thread_id) per existing rules
)
chain.step_index = next_leg_index
persist(parent.active_chain = chain)
audit_log_chain_auto_advance(parent.id, chain, triggering_child=child)
# Do NOT wake parent.
return
```

### Auto-appended Prior Leg Context

Every non-first leg's brief is suffixed (not prepended — the manager's brief is the primary instruction surface) with a stable orchestrator-generated block:

```
---
## Prior leg context (auto-generated by orchestrator)

Prior leg:    TASK-579  (agent: senior_dev)
Status:       completed
Verdict:      APPROVE
Confidence:   92
Summary:
  <CompletionReport.output_summary verbatim>

Artifact dir: workspaces/senior_dev/artifacts/TASK-579/
---
```

`artifact_dir` is included only when the prior leg's report set it.

The string template lives in one helper (`src/orchestrator/chain.py:build_prior_leg_context(child_task, report) -> str`) so audit reproduction and tests can call it directly.

### Step-budget accounting

The 50-step cap (`GRASSLAND_MAX_ORCHESTRATION_STEPS`, enforced via `tasks.orchestration_step_count`) was designed to detect runaway manager indecision. A chain is one decision; auto-advances are the orchestrator executing it.

- **Declaring a chain** increments `orchestration_step_count` by exactly **1** (same as a single-leg delegate today — uses the existing `try_claim_for_step` CAS on the manager's session).
- **Each `chain_auto_advance`** writes an audit row but does **NOT** bump `orchestration_step_count`.
- **The final-leg wake** (when chain completes successfully and parent wakes) goes through the existing wake path; the manager's next session starts and consumes one step against the cap normally.

This is the budget unlock. A clean small-item run (dev_agent build → senior_dev review → qa_engineer QA → manager merge+done) takes **4 step-count today** (one per manager wake); under chains it takes **2 step-count** (declare-chain + final-wake-to-merge). The two auto-advances in between are free. Revise-heavy items still benefit on their non-revise portion — every REVISE iteration that the manager handles is one step today, one step under chains; only the routine forward-motion portion compresses.

Concrete projection on TASK-577's small-item wakes: items 7 and 8 (no REVISE iterations) drop 3 → 2; items 1a, 1c, 2 (each had one REVISE round) drop 5 → 4. The bigger payoff is on Item 6's six phases — each phase has a 3-leg gate chain, and the LARGE workflow consumed ~23 of the 50 wakes.

### Observability

**`grassland details TASK-X`** gets a "Current workflow chain" block when `active_chain` is set:

```
Current workflow chain (step 2 of 3):
  ✓ Leg 1  dev_agent     → TASK-579  completed   (verdict: -, advanced)
  ▶ Leg 2  senior_dev    → TASK-580  in_progress (expecting: APPROVE)
  ⋯ Leg 3  qa_engineer                            (expecting: PASS)
  Declared at orchestration_step #14 (2026-05-30T03:22:11Z)
```

Rendering source: `parent.active_chain` + the spawned child tasks identified via `parent_task_id` filter, ordered by `created_at`. Children-from-different-step-indexes are distinguishable because `active_chain` is cleared between chains (so only the in-flight chain's children show in this block; previously-completed chains' children are visible via the existing children list).

**Audit log** gets a new action `chain_auto_advance`:

```json
{
  "leg_index": 2,
  "spawned_child_id": "TASK-581",
  "triggering_child_id": "TASK-580",
  "triggering_verdict": "APPROVE",
  "chain_origin_step_audit_id": 4521
}
```

`grassland audit TASK-X --action chain_auto_advance` filters to chain transitions only. `--action orchestration_step` continues to surface only manager decisions, so the founder can read "what counted against the 50-cap" cleanly.

**Web UI.** The existing task detail route already returns the full task row; `active_chain` is exposed in the existing response (no new endpoint). The UI renders a thin chain-strip component above the orchestration-steps timeline. The OpenAPI snapshot test (`tests/contract/test_openapi_snapshot.py`) needs regeneration via `GRASSLAND_REGEN_OPENAPI=1`; the TS mirror at `web/src/lib/api/tasks.ts` and its types update accordingly.

**Wake reason on parent.** When the parent wakes (chain complete or aborted), the manager's invocation prompt's history block gets a one-line chain summary:

- Success: `Chain summary: 3 legs, all completed (TASK-579 → TASK-580 → TASK-581), final verdict PASS`
- Mismatch: `Chain aborted at leg 2: senior_dev returned REQUEST_CHANGES (expected APPROVE); see TASK-580`
- Blocked: `Chain aborted at leg 2: TASK-580 self-blocked (see its summary)`
- Failed: `Chain aborted at leg 2: TASK-580 failed (auto-revisit cap exhausted)`

The manager doesn't need to re-derive the chain's history from raw child task records.

## Failure modes & edge cases

| Scenario | Behavior |
|---|---|
| Verdict mismatch on gated leg | Chain aborts at that leg; `active_chain` cleared; manager wakes with the mismatched report. Manager handles iteration (existing REVISE flow). |
| Worker emits verdict when none expected | Ignored for routing (no gate); still stored on `task_results` and visible in `details`. Cheap forward-compat. |
| Worker emits `status=blocked` mid-chain | Chain aborts; manager wakes with the blocker text. |
| Worker on gated leg emits no verdict | Treated as mismatch (`None ≠ "APPROVE"`); chain aborts. Forces gated-leg workers to actually emit their verdict — surfaces silent drift early. |
| Auto-revisit on opaque infra failure mid-leg | The failed leg cascade-fails the chain parent per existing logic (`_maybe_spawn_auto_revisit` runs before `_enqueue_parent_if_waiting`; cascade-fail still cascades when `root_auto_revisit_spawned=True`, only the Feishu notification is suppressed). `active_chain` is cleared as part of the parent's cascade-fail. The auto-revisit spawns a NEW root task carrying `revisit_of_task_id` per the existing "auto-revisit creates a new root" invariant — it does NOT preserve `parent_task_id`, and is therefore NOT re-attached to the original chain. Chains do not survive an opaque leg failure in v1; the founder (or any successor driver auto-revisited from the chain parent) sees the failed parent and decides whether to re-declare the chain skipping completed legs. Durable worker artifacts (per memory `durable-worker-artifact-rescues-orphaned-completion`) survive the cascade so the re-declared chain can pick up evidence. |
| Leg's spawned child fails to launch | Treated as leg failure → manager wakes. Same code path as today's failed-delegate. |
| Founder cascade-cancels parent | Existing cascade kills any in-flight leg; clears `active_chain` as part of the parent-cancel path. |
| Founder cancels a leg directly (no cascade) | Wake parent; clear `active_chain`. Explicit termination beats auto-advance. |
| Founder revisits parent mid-chain | Cannot happen — `grassland revisit` requires terminal predecessor; parent is `blocked(delegated)` while chain runs. |
| Manager declares chain with off-team agent (any leg) | Validated at decision-parse time across all legs; rejected as feedback step back to manager (existing `feedback` mechanism). No partial spawn. |
| Manager declares chain referencing themselves | Same path as cross-team guard — managers are not in the team's worker registry; rejected. |
| Empty `then: []`, no `expect_verdict` | Identical to today's single-leg delegate. Allowed (zero-cost forward-compat). |
| Empty `then: []`, `expect_verdict` set | 1-leg gated chain. First leg's worker must emit matching verdict; mismatch wakes manager, match wakes manager too (next-leg-doesn't-exist branch). Useful when the manager wants verdict-gated wake context without writing a multi-leg chain. |
| Nested chains (chain-leg worker emits a `decision` field) | Workers don't speak the NextStep protocol (`run_step.py:295` gate); `decision` from a worker is silently ignored today. Same here. |
| Chain step_audit_id payload is missing the first-leg's expect_verdict | Defensive — `first_leg_expect_verdict` lives in the chain payload itself, not derived from audit. If the chain payload was written, the field is present. |

## Testing

### Unit (no daemon)

- `NextStep` model accepts `then`, `expect_verdict`; serialization round-trip with 0 / 1 / 3 legs.
- `CompletionReport` accepts optional `verdict`; missing field deserializes to `None`.
- `_build_prior_leg_context` produces the documented template; includes `artifact_dir` only when set; verbatim-preserves multiline summaries.
- Chain advance happy-path: matched verdict, next leg spawn (mocked DB).
- Chain advance verdict mismatch: chain cleared, parent waked.
- Chain advance status=blocked: chain cleared, parent waked.
- Chain advance final leg: chain cleared, parent waked with success summary.
- Cross-team guard rejects chain with any off-team leg (test each position).
- Step-count accounting: declaring a 4-leg chain bumps `orchestration_step_count` by 1; three `chain_auto_advance` rows have NO `orchestration_step_count` effect; the final-leg parent-wake bumps the counter by 1 when the manager's next session claims (totals to 2 per chain, not 4).
- Auto-revisit + chain: simulated session_timeout on leg 2 → chain parent cascade-fails, `active_chain` cleared, auto-revisit root spawned independently of the chain. Assert the auto-revisit root has no `parent_task_id` and no chain inherited.

### Integration (fake-claude harness)

- Plan a 3-leg chain (`dev_agent` builder → `senior_dev` reviewer → `qa_engineer` QA) where the fake reviewer emits `verdict: APPROVE` and fake QA emits `verdict: PASS`. Assert: exactly 2 `chain_auto_advance` rows, exactly 1 final manager wake, `orchestration_step_count` on parent incremented by 2 total (declare + final wake).
- Same plan, fake reviewer emits `REQUEST_CHANGES`. Assert: 0 `chain_auto_advance` rows; manager wakes after leg 2 with mismatch context in history block; `active_chain` cleared on parent.
- Founder-cancel during leg 2 in-flight: assert `active_chain` cleared, no leg 3 spawn, parent ends in `failed-cancelled`.
- Auto-revisit on leg 2 (fake worker triggers session timeout under cap): assert chain parent cascade-fails, `active_chain` cleared on parent, auto-revisit root spawned with no `parent_task_id` and no chain inheritance, no leg 3 spawn.

### Contract

- OpenAPI snapshot regen (`tests/contract/test_openapi_snapshot.py`) — task detail response includes `active_chain` field.
- TS coverage (`web/src/test/openapi-coverage.test.ts`) — no new routes, so EXCLUDED set untouched; type definitions for `active_chain` added in `web/src/lib/api/tasks.ts`.

## Migration & rollout

- **Schema:** one ALTER TABLE per org SQLite at daemon lifespan startup, idempotent (existing migration pattern). NULL default means every existing task is correctly "no chain."
- **Code:** all paths are null-safe — `active_chain is None` short-circuits to the existing single-leg behavior at every branch point.
- **Bootstrap docs:** no agent prompt changes. Workers learn the `verdict` field via an addition to the `start-task` skill's completion-payload template (one bullet: "If your role is to issue a verdict, add `\"verdict\": \"<value>\"`"). Managers learn the chain shape via an addition to the per-team workflow KB entry (`engineering-task-workflow` first; other teams pick it up when ready).
- **No flag, no phased rollout.** The feature is null-safe. Existing single-leg delegates continue to work unchanged.

## Load-bearing invariants

- **`active_chain` is cleared on every parent wake.** No code path that wakes the parent may leave a stale chain pointer. Centralize the clear in `_enqueue_parent_if_waiting`.
- **Chain auto-advances do NOT bump `orchestration_step_count`.** This is the whole point of the feature. Test it directly; regression would silently re-poison the budget calculus.
- **Cross-team validation runs on every leg, at declaration time.** Don't lazy-validate when the leg is about to spawn — by then the manager's session has ended and the feedback loop is broken.
- **`first_leg_expect_verdict` lives in the chain payload, not derived from audit.** Audit-row recovery would couple the orchestrator to audit-log durability for routing decisions; the chain payload is the source of truth.
- **Workers don't author chains.** The `is_team_manager(agent)` gate in `run_step.py` already blocks workers from emitting `decision` payloads; do not relax this when adding chain support.
- **Auto-revisit of a chain leg does NOT preserve the chain.** Auto-revisit follows the existing "spawns a new root, cascade-fails the ancestor" invariant; chains aborted by cascade-fail do not auto-resume. v1 accepts this — the founder/successor decides whether to re-declare. Re-attaching auto-revisits to chains would require breaking the new-root invariant and is deferred.
- **Cancel-during-chain clears `active_chain` before terminating the in-flight leg.** Otherwise the leg's terminal could race the cancel and trigger a phantom auto-advance.

## Open questions

None. Brainstorm settled all five fork-questions (Q1 verdict-aware happy path; Q2 free-string verdicts; Q3 auto-append prior leg context; Q4 final-leg wakes manager; storage on parent task as `active_chain` JSON column).
