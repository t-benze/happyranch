# Orchestrator: Routing, Permissions & State

The application layer that drives the organization — task routing, inter-team communication, permissions, and the task state machine.

---

## 1. Orchestrator Responsibilities

The orchestrator is the application code that ties everything together. It spawns executor-backed agent sessions, feeds manager decisions back into a loop, routes work between teams, and persists every step.

```
┌─────────────────────────────────────────────────┐
│                  ORCHESTRATOR                     │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Escalation│  │  Audit   │  │  Performance  │  │
│  │  Router   │  │  Logger  │  │   Tracker     │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │Inter-Team │  │ Knowledge│  │   Founder     │  │
│  │  Comms    │  │   Base   │  │  Dashboard    │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │         Agent Executor Abstraction        │    │
│  │   Claude Code │ Codex │ OpenCode │ …      │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
        │              │              │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Content  │   │ Product │   │   Ops   │   │   CX    │
   │  Team    │   │  Team   │   │  Team   │   │  Team   │
   └─────────┘   └─────────┘   └─────────┘   └─────────┘
```

### What the orchestrator does

**1. Receives work requests** and routes them to the right Team. A new content brief goes to Content Team. A partner application goes to Ops Team. A bug report goes to the Product & Engineering Team.

**2. Manages inter-Team communication.** When the Content Team publishes a guide, it notifies the CX Team so Support Agent knows about new content. When the Product & Engineering Team changes a payment flow, it triggers a cross-audit task in the Ops Team. These are not internal to any one Team — the orchestrator handles the handoff.

**3. Runs the escalation router.** When an agent calls the `escalate` tool, the orchestrator evaluates the 12 escalation rules (from `04-escalation-rules.md`) and either routes to the relevant manager's Team or sends a notification to the founder.

**4. Manages the revision loop.** When QA returns REVISE, the orchestrator tracks the revision count and either re-triggers the Content Team with feedback or escalates after max rounds.

**5. Audits delegations.** After each delegated child task terminates, the orchestrator writes an implicit `review_verdict` audit row (`approved` for COMPLETED, `rejected` for FAILED). The founder reviews these via `happyranch audit` to identify which agents need attention. (The legacy 30-day rolling tier classification was removed on 2026-05-27 — see §2.)

**6. Assembles agent context.** Before each session, the orchestrator gathers the system prompt, learnings file, team health, and task-specific context, then writes them into the agent's workspace in the format expected by the configured executor.

**7. Provides the founder dashboard.** Aggregates audit logs, escalation summaries, and team health metrics into a weekly report.

### Inter-Team communication patterns

| Trigger | From Team | To Team | Payload |
|---------|-----------|---------|---------|
| Content published | Content | CX | New guide summary + URL for Support Agent |
| Payment flow change proposed | Product | Ops | Change spec for Compliance Agent cross-audit |
| Compliance audit finding on payment | Ops | Product | Finding + recommended fix for Payment Agent |
| Recurring support issue identified | CX | Content | Feedback ticket requesting guide update |
| Recurring support issue (feature gap) | CX | Product | Feature request with user data |
| Partner communication drafted | Ops | Content | Draft for brand voice review |
| CX feature request submitted | CX | Product | Feature request for feasibility check |

### Implementation approach

The orchestrator is a Python application that:
- Instantiates the 4 Teams with their agents and task templates
- Exposes an API (or CLI) for submitting work requests
- Maintains state in a database (SQLite for prototype, PostgreSQL for production)
- Runs agent sessions via the executor abstraction (not all running simultaneously)
- Listens for escalation signals and inter-team communication
- Persists audit logs and agent memory

---

## 2. ~~Performance Tier Impact on Team Configuration~~ (REMOVED)

The performance-tier feature was removed on 2026-05-27. The audit log
(implicit `review_verdict` rows after every delegation, plus completion /
failure events) is sufficient for the founder to identify which agents
need attention via `happyranch audit`. Tier classification on top of the
verdicts added no behavioral enforcement in code, and the per-agent tier
prose in agent `.md` files was not actionable (workers never saw their
own tier; managers saw worker tiers but the tier didn't gate delegation).

---

## 3. Permission and Authority Model

### Approach: executor-native sandboxing + system prompt guardrails

Agents run through their configured executor. Claude sessions use `claude --permission-mode auto` plus a narrow `Bash(happyranch:*)` allow rule for callbacks. Codex sessions use `codex exec` with the configured sandbox mode. opencode sessions use `opencode.json` for bash permission mapping. Pi sessions use `pi -p ... --mode json` and have no HappyRanch-managed sandbox or permission file. Permissions are otherwise generous — agents can read, write, and execute within their workspace.

**Founder-concern boundaries** (the only things that truly need restricting) are enforced through two layers:

1. **System prompt** — each agent's bootstrap doc (`CLAUDE.md` or `AGENTS.md`) explicitly states what it cannot do. The agent is instructed to call `escalate()` when it encounters these boundaries.
2. **Orchestrator post-session review** — the orchestrator inspects completion reports and audit logs for violations. If an agent somehow bypasses its system prompt instructions, the orchestrator catches it and escalates.

This approach avoids building a complex custom permission layer. The executor handles low-level sandboxing, while the system prompt provides the "soft" guardrails and the orchestrator provides the "hard" backstop.

### What counts as a founder-concern boundary

Per the org charter, these are the ONLY restrictions that matter:

| Boundary | Enforced by |
|---|---|
| No `git push` to main / production deploy | System prompt + orchestrator review |
| Spend >$200 single or >$100/month recurring | System prompt → escalation tool |
| Raw payment card data storage (PCI-DSS) | System prompt + orchestrator review |
| Political sensitivity in content | System prompt → escalation tool |
| Refunds >$150 | System prompt → escalation tool |
| Downtime >30 minutes | System prompt → escalation tool |

Everything else — file access, shell commands, network requests, git operations on feature branches — is auto-approved.

### What happens when an action is blocked

There are four types of permission blocks, each handled differently:

#### Type 1: Out-of-scope action
**What**: Agent tries something outside its role entirely.
**Example**: Content Writer tries to run `git push` or modify `src/payments/stripe.py`.
**Response**: Executor blocks immediately. Agent receives: "Permission denied: file write to src/payments/ is outside Content Writer scope. This is Payment Agent's domain."
**Agent behavior**: Notes the blocker in its completion report under "dependencies." Completes everything else it can.
**Orchestrator action**: Logs the attempt. No further action needed — the system worked correctly.

#### Type 2: Needs higher authority
**What**: Agent needs approval that exceeds its authority level.
**Example**: CX Manager tries to approve a $200 refund (above $150 limit). Ops Manager wants to agree to a 6-month partner contract (above 3-month limit).
**Response**: Agent calls `escalate(category="budget", severity="medium", summary="Refund of $200 requested by tourist for cancelled tour. Exceeds my $150 authority.")`.
**Task state**: Moves to `waiting_for_approval`. The agent completes all other work on the task and submits a completion report with the pending approval clearly noted.
**Orchestrator action**: Routes the escalation per the 12 rules in `04-escalation-rules.md`. Creates a founder notification with the agent's summary and recommendation. Holds the specific blocked step (not the entire Team). non-root tasks do not escalate directly to the founder.
**Resolution**: Founder approves or denies via the dashboard. Orchestrator resumes the task with the decision injected into context.

#### Type 3: Needs another agent's work
**What**: The task has a cross-agent or cross-team dependency.
**Example**: Payment Agent proposes a payment flow change, but it needs Compliance Agent review before Engineering Head can approve. Dev Agent needs to implement a feature that requires a partner API endpoint, but Partner Liaison hasn't onboarded that partner yet.
**Response**: Agent identifies the dependency in its completion report: "Blocked on: Compliance Agent cross-audit of this payment flow change. Cannot proceed until PCI-DSS and cross-border compliance is verified."
**Task state**: Moves to `blocked_on_dependency`.
**Orchestrator action**: Reads the dependency from the completion report. Spawns the required agent session (Compliance Agent in the Ops Team) with the dependency context. The blocking task is queued, not abandoned.
**Resolution**: Once the dependency agent completes its work, the orchestrator resumes the blocked task with the dependency result injected into context.

#### Type 4: Ambiguous or novel situation
**What**: Agent encounters something not covered by existing permissions or SOPs.
**Example**: Content QA discovers content that might be politically sensitive but isn't sure. Compliance Agent finds a regulation that could be interpreted two ways.
**Response**: Agent calls `escalate(category="novel", severity="medium", summary="...")` with its best assessment and a recommendation.
**Task state**: Moves to `waiting_for_guidance`.
**Orchestrator action**: Routes to founder. The agent's recommendation is included so the founder can often just approve/deny rather than research from scratch.
**Resolution**: Founder runs `happyranch resolve-escalation` to clear the task and — when the ruling should bind future occurrences — writes a KB entry via `happyranch kb add` (with `source_task: <task-id>` in frontmatter) so the next agent finds the answer without re-escalating.

### Task state machine

#### States (7)
- **pending** — created; no agent subprocess started yet.
- **in_progress** — an agent subprocess is running, OR the task is a parent waiting on its own children/jobs. A parent waiting on its own children/jobs stays `in_progress`; the waiting reason is recorded in `block_kind` (`delegated` = waiting on one or more child subtasks to terminate; `blocked_on_job` = waiting on one or more background jobs to reach a terminal state, set when a completion report carries a non-empty `waiting_on_job_ids`); `block_kind IS NULL` ⟺ a subprocess is running now.
- **escalated** — waiting on the founder (via `happyranch resolve-escalation`); was `blocked(escalated)`.
- **completed** — terminal, success.
- **failed** — terminal, unsuccessful.
- **cancelled** — terminal; founder-initiated stop, distinct from `failed`.
- **resolved_superseded** — terminal. An `escalated` / `in_progress(delegated)` task closed because a human-authorized continuation (founder `revisit`, or a founder/manager thread-dispatch) superseded it; the close cites the successor task and does **not** re-run the work.

> **Deprecated — `blocked` (fully retired Phase 3).** Before THR-037 Change B (Path B, stored source-of-truth), the surfaced vocabulary used a single `blocked` state discriminated by `block_kind` (`delegated`/`escalated`/`blocked_on_job`). Path B collapsed it; the value was retained for the transition window + reverse migration and was fully retired in Phase 3 after a soak.

#### Failure-recovery contract (TASK-573, THR-028)

When a subtask reaches a terminal state, the orchestrator evaluates the parent task
for advancement. If any subtask FAILED (rather than COMPLETED), the parent is NOT
cascade-failed. Instead:

1. **Bounded manager-wake.** The parent task (a task with `task_type='task'`) is
   re-enqueued for a fresh manager decision step. The failed subtask's reason
   (`note` + completion report / error context) is available to the parent so it
   can author an updated brief and re-delegate.

2. **Round bound.** At most 2 re-spawn rounds per delegation slot. The round count
   is derived from EXISTING database state (count of FAILED subtasks of this
   parent) — no schema migration, no new/alter/overload column. Each child
   failure that re-wakes the parent consumes one round.

3. **Exhaustion escalation.** When the round bound is exhausted (> 2 FAILED
   subtasks in this delegation slot), the parent transitions to
   `escalated` via `db.try_escalate()`, carrying the last failure
   reason. The parent does NOT cascade-fail — the founder can resolve the
   escalation per existing routes. non-root tasks never escalate directly — they fail and hand back to their parent; only the (root) parent escalates on exhaustion.

4. **Chain-leg failure.** When a workflow chain leg fails (subtask is FAILED, not
   COMPLETED), the chain does NOT cascade-fail the parent. Instead, the
   chain is cleared and the parent is handed back to its manager decision step
   (subject to the same 2-round bound and exhaustion escalation).

5. **Happy path unchanged.** All subtasks COMPLETED → parent advances to its
   next decision step (existing behavior). REVISE-verdict auto-advance is
   unchanged.

#### Fan-out (parallel delegation, Phase 1)

A manager may declare a fan-out decision (`action: fanout`) to spawn N children
in parallel (2 ≤ N ≤ 8, read-only only). The orchestrator:

1. **Validates** width, width_cap_ack, workspace presence, scope, and rejects
   per-child `then`/`expect_verdict` (read-only Phase 1 only; mutating fan-out
   is out of scope).
2. **Atomically mints** all N children via `try_delegate_many`, transitioning
   the parent to `in_progress(delegated)` with `active_fanout` set (an additive
   JSON metadata column).
3. **Parks** the parent — the existing `DELEGATED` barrier wakes it once when
   all N children are terminal (same CAS as single-child delegation).
4. **Injects join context** into the manager's wake prompt: a structured block
   listing each child's id, agent, status, summary excerpt, output_dir, and
   failure note.
5. **Clears** `active_fanout` after successful join claim or terminal parent
   close.

Failure-join reuses bounded failure-recovery (§Failure-recovery contract):
failed fan-out children individually consume re-spawn rounds; the parent wakes
on each terminal child, and exhaustion escalates the parent after
`_FAILURE_ROUND_BOUND` (2) failed children. No partial-join or cascade-fail
semantics are introduced.

Startup recovery (daemon restart) re-enqueues parked `in_progress(delegated)`
fan-out parents when all children are already terminal (same as
single-delegation). The join context is built from persisted audit rows when
the CAS winner processes the wake.

#### Transitions

```
pending → (run_step pickup) → in_progress → { completed | failed | cancelled | in_progress(delegated) | in_progress(blocked_on_job) | escalated }

in_progress(delegated) → (all children terminal) → in_progress (re-entry, block_kind cleared on claim)
in_progress(blocked_on_job) → (all blocking jobs reach terminal state; _maybe_resume_blocked_task enqueues while the row stays in_progress) → in_progress (run_step CAS admits exactly one on pickup, clearing block_kind)
escalated → (POST /resolve-escalation approve) → pending (re-enqueued; manager's next prompt carries an ESCALATION RESOLVED header with the founder's rationale)
escalated → (POST /resolve-escalation reject)  → failed (cascade-fails the parent if any)
escalated | in_progress(delegated) → (revisit / thread-dispatch names it in lineage) → resolved_superseded (terminal; block_kind cleared, audit cites the continuation root task_id; NO re-enqueue. The delegated close is gated on all children being terminal and never cascade-SIGTERMs live siblings)
escalated → (POST /resolve-escalation approve on exhaustion escalation) → pending (re-enqueued; parent carries the exhaustion context + failure reason from the failed subtask — manager can re-ground and re-delegate)
(any non-terminal) → (founder cancel) → cancelled
```

#### Execution model

The orchestrator exposes exactly one primitive: `Orchestrator.run_step(task_id)`.
It picks up a task that is `pending` or `in_progress(delegated)` with all children
terminal, invokes its `assigned_agent` once, classifies the result, persists
the transition, and enqueues the next task to advance. Recursion is via queue
re-entry — no loops inside `run_step`. A task that is `in_progress` with
`block_kind IS NULL` is a *live subprocess* and is never re-admitted (admitting
it would double-spawn).

Budget: each `run_step` call increments `orchestration_step_count` persisted
on the task. When the count exceeds `max_orchestration_steps` the task parks
in `escalated` for founder review (root tasks only; a non-root over-budget task fails and hands back to its parent).

#### External waits: PR CI and similar systems

The orchestrator does not gain new task states for external systems such as GitHub CI. External waits use jobs. A task that cannot continue until an external job finishes reports `status="blocked"` with non-empty `waiting_on_job_ids`; the row parks as `in_progress` with `block_kind='blocked_on_job'`; and the existing all-terminal job predicate resumes the task.

PR CI completion is the canonical example. The PR CI / guarded merge helper is a bounded job that polls GitHub for a pinned PR head SHA, exits terminal with a structured verdict, and wakes the task through `blocked_on_job_ids`. The orchestrator never infers PR success from PR creation, GitHub mergeability alone, or an absent check list.

### Timeout handling

Blocked tasks don't wait forever:

| Block type | Default timeout | On timeout |
|---|---|---|
| Waiting for founder approval | 24 hours | Re-notify founder + flag in dashboard as urgent |
| Blocked on dependency (same team) | 2 hours | Escalate to manager agent |
| Blocked on dependency (cross-team) | 4 hours | Escalate to both managers |
| Waiting for guidance (novel situation) | 24 hours | Re-notify founder + agent proceeds with conservative default if one exists |

The orchestrator runs a background check every 15 minutes for timed-out tasks. If a founder approval times out twice (48 hours total), the task is flagged as critical on the dashboard through whatever channel the founder has configured.

### Permission evolution

Permissions aren't static. As the system matures:
- Novel situations that the founder resolves become codified rules — the orchestrator updates the permission config and knowledge base so that situation is handled automatically next time
- The founder can adjust any agent's permissions at any time via the dashboard

### Reviewer/QA verdict discipline

Review and QA leg tasks (code_reviewer, qa_engineer) MUST complete their leg
with a verdict (APPROVE / REVISE / PASS / FAIL) and MUST NOT self-block. A
completion report with `status=blocked` and an EMPTY `waiting_on_job_ids` is a
MALFORMED report — the leg is treated as FAILED, and the parent wakes for a
manager decision step (not cascade-failed). Self-blocked reviews that omit a
verdict waste the delegation and burn a re-spawn round.
