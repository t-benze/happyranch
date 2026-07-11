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
│  │   Claude Code │ Codex │ OpenCode │ Pi │ … │    │
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
**Resolution**: Founder resolves via Continue or Cancel. On Continue the orchestrator re-enqueues the task to pending and injects the founder's input into the manager's next-step prompt. On Cancel the task terminates in CANCELLED (cancelled_at set) with no resume/context injection.

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
**Resolution**: Founder runs `happyranch resolve-escalation --decision continue` (to resume the work) or `--decision cancel` (to terminate it) to clear the task and — when the ruling should bind future occurrences — writes a KB entry via `happyranch kb add` (with `source_task: <task-id>` in frontmatter) so the next agent finds the answer without re-escalating.

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

#### Failure-recovery contract (TASK-573, THR-028, THR-078)

When a subtask reaches a terminal state, the orchestrator evaluates the parent task
for advancement. If any subtask FAILED (rather than COMPLETED), the parent is NOT
cascade-failed. Instead:

1. **Bounded manager-wake.** The parent task (a task with `task_type='task'`) is
   re-enqueued for a fresh manager decision step. The failed subtask's reason
   (`note` + completion report / error context) is available to the parent so it
   can author an updated brief and re-delegate.

2. **Owner-adjudication primary (THR-078).** Any fan-out round with ≥1 non-clean
   slice packs per-slice terminal context (status + verdict + confidence + note +
   output_dir) and wakes the root owner to adjudicate — the orchestrator does NOT
   auto-escalate to founder on a mixed round. The owner classifies each slice
   (merge greens / re-dispatch legit REQUEST_CHANGES as revise / drop no-ops /
   retry genuine failures). Applies uniformly to benign AND real failures.

3. **Per-slice retry ceiling (THR-078).** A per-slice retry ceiling of 1
   replaces the old count-based `_FAILURE_ROUND_BOUND` (2). The owner may
   re-drive a given slice ONCE; if that same slice fails AGAIN (its 2nd failure),
   the orchestrator forces escalation to founder. The guard moves from 'count of
   FAILED siblings anywhere in the fan-out' to 'this specific slice is genuinely
   stuck after one retry'. Per-slice retry count is derived from EXISTING database
   lineage: the child's `revisit_of_task_id` chain (no schema migration). When a
   fan-out owner re-dispatches a failed slice, the new child carries
   `revisit_of_task_id` pointing to the failed predecessor; if that retry child
   also fails, the orchestrator detects the revisit ancestor within the same
   parent and escalates.  **The `revisit_of_task_id` field is MANDATORY** —
   a re-delegate to an agent with a FAILED child under the same parent that
   omits this field is HARD-REJECTED (feedback, no child spawned), even on
   the first retry.  Only FAILED ancestors count toward the ceiling; a retry
   of a COMPLETED predecessor does not trigger escalation on its first failure.

4. **Exhaustion escalation.** When the per-slice ceiling is exhausted (a slice's
   2nd failure), the parent transitions to `escalated` via
   `db.try_escalate()`, carrying the last failure reason. The parent does NOT
   cascade-fail — the founder can resolve the escalation per existing routes.
   non-root tasks never escalate directly — they fail and hand back to their
   parent; only the (root) parent escalates on exhaustion.

5. **Chain-leg failure.** When a workflow chain leg fails (subtask is FAILED, not
   COMPLETED), the chain does NOT cascade-fail the parent. Instead, the
   chain is cleared and the parent is handed back to its manager decision step.

6. **Happy path unchanged.** All subtasks COMPLETED → parent advances to its
   next decision step (existing behavior). REVISE-verdict auto-advance is
   unchanged.

#### Fan-out (parallel delegation)

A manager may declare a fan-out decision (`action: fanout`) to spawn N children
in parallel (2 ≤ N ≤ 8). The orchestrator:

1. **Validates** width, width_cap_ack, workspace presence, and scope. A child may optionally carry `then`/`expect_verdict` — a *pipeline carrier* (Phase 2) — whose legs are validated exactly like an inline `delegate + then` chain (each leg needs `agent` + `prompt`).
2. **Atomically mints** all N children via `try_delegate_many`, transitioning
   the parent to `in_progress(delegated)` with `active_fanout` set (an additive
   JSON metadata column). For pipeline carriers, the child's inline chain is
   materialized on its own row (see Pipeline carriers below).
   **Child task_type:** a child targeted at a team manager receives
   `task_type='task'` so its delegate-chain decisions are parsed (mutating
   fan-out, THR-056 msg39); a child targeted at a worker receives
   `task_type='subtask'` (read-only). Pipeline carriers are always `subtask`
   (they never run agent sessions of their own).
3. **Parks** the parent — the existing `DELEGATED` barrier wakes it once when
   all N children are terminal (same CAS as single-child delegation).
4. **Injects join context** into the manager's wake prompt: a structured block
   listing each child's id, agent, status, summary excerpt, output_dir, and
   failure note.
5. **Clears** `active_fanout` after successful join claim or terminal parent
   close.

**No fan-out review gate (THR-012 msg 129/131).** The width cap (8) is a
pure machine-resource limit — children are spawned immediately at any width
2–8. The former `pending_review` status and `review_required` job gate are
removed. The real control over what code lands is the per-PR merge gate:
every mutating child opens its own PR requiring `code_reviewer` APPROVE +
`qa_engineer` PASS + CI + founder/EM merge. The founder cannot add useful
judgment to "6 vs 8 children" — it is a resource question for the runtime.

**Pipeline carriers (Phase 2).** A fan-out child that carries a non-empty `then` is a *carrier*: on spawn the orchestrator materializes its inline chain (`active_chain` on the child's row, via the same path as an ordinary `delegate + then`) instead of dispatching a bare read-only child. The composition is safe because `active_fanout` lives on the parent's row and `active_chain` lives on each child's row — **two independent columns on two different rows, never the same row, so there is no clobber** (the two-column-two-row invariant). Carrier detection is schema-free: a carrier is any task whose id is in its parent's `active_fanout.children_ids` and which has a non-empty `active_chain`; no new column. **Lifecycle rule: a carrier reaches a terminal state only after its own chain completes.** When a carrier's final leg matches its `expect_verdict`, the carrier has no session of its own to run — it terminates directly and feeds the parent's fan-out barrier (`_enqueue_parent_if_waiting`) without waking a manager. A carrier's internal legs never wake the parent; only the carrier's own terminal status counts toward the barrier.

**Mutating children (THR-056 msg39, option 3).** A fan-out child targeted at a
team manager that does NOT pre-declare `then`/`expect_verdict` (i.e., a plain PENDING
child, not a pipeline carrier) receives `task_type='task'` instead of
`task_type='subtask'`. Its agent session runs, and when it returns a
`delegate` decision with an inline chain (`then` legs), the orchestrator
parses the decision (since `task_type='task'`) and spawns the implementation
subtree inside that branch using the standard chain mechanism. The child parks
as `in_progress(delegated)` with `active_chain` set, and `_enqueue_parent_if_waiting`
handles chain auto-advance exactly as for a top-level inline delegate chain.
When its chain completes, it terminates and feeds the original fan-out
parent's barrier. The fan-out parent does not join until ALL children
(including mutating ones) are terminal. A mutating child's internal chain legs
do not count toward the fan-out parent's barrier — only the mutating child's
own terminal status does.

Failure-join (THR-078): any fan-out round with ≥1 failed child wakes the
root owner with structured per-slice join context (status + verdict +
confidence + note + output_dir) — the orchestrator does NOT auto-escalate
based on failed-sibling count.  The owner adjudicates each slice.  A
retained per-slice retry ceiling (ceiling = 1) fires escalation only when
the SAME slice fails twice: the re-dispatched child carries
`revisit_of_task_id` pointing to the failed predecessor, and the
orchestrator detects the revisit ancestor within the same parent.  For a
pipeline carrier this is **fail-closed at the carrier**: a leg
verdict-mismatch or a failed leg fails the whole carrier (no partial-chain
completion), and the failed carrier then feeds the parent's barrier exactly
as any failed child does.  No partial-join or cascade-fail semantics are
introduced.

**Worktree isolation (mutating fan-out).** Each mutating child inherits the
make-worktree pattern: it receives its own git worktree on a per-task branch
(`task/<task_id>`), edits its disjoint file set, commits, and pushes. The
child's worktree is created at spawn and torn down after completion.
Cost (~200-500ms + disk per child) is bounded by MAX_FANOUT_WIDTH=8.

**Integration model (a).** Each mutating child opens its own PR. The parent
join surfaces each child's PR reference (number/URL) when present in the
child's output, using the existing join-context child output (no new PR
entity, no new schema). The parent join summarizes outcomes; the founder or
EM merges each child PR individually through the normal per-PR gates.

**Shared-file serial doctrine.** Children must own DISJOINT file sets — it
is the manager's responsibility to partition work so no two children touch
the same file. Shared-file convergence does NOT route through a fan-out
child; it routes through a SERIAL follow-up delegate spawned by the manager
after the fan-out join. This is a binding design rule, not a runtime
enforcement (the manager brief carries the obligation).

**Fail-closed at the child.** A failed mutating child discards its worktree
and cascades per bounded failure-recovery. No partial integration — if one
child fails, the parent's join context shows the failure and the manager
decides next steps (retry, revise, escalate). Successful children are not
rolled back; their PRs remain open for independent merge.

Startup recovery (daemon restart) re-enqueues parked `in_progress(delegated)`
fan-out parents when all children are already terminal (same as
single-delegation). The join context is built from persisted audit rows when
the CAS winner processes the wake.

#### Daemon restart recovery — pid-liveness probe (THR-079)

On daemon restart, tasks that were `in_progress` with `block_kind IS NULL`
(i.e., had a live executor subprocess) are NOT assumed dead. Instead, the
sweep reads the persisted `executor_pid` (set at session start by the
orchestrator's `_on_started` closure) and probes the OS with `os.kill(pid, 0)`:

| Probe result | Action |
|---|---|
| pid ALIVE | **Leave alone** — session survived the restart; no reconcile. |
| pid DEAD (`ProcessLookupError`) | **FAILED** with reason "session died on daemon restart — executor pid not alive". |
| pid NULL or probe inconclusive (`PermissionError`, etc.) | **FAILED** with reason "session liveness undeterminable on daemon restart" (fail-closed default). |

No auto-revisit is spawned for any of these outcomes — the founder receives
a `daemon_restart_failure` audit row and decides whether to re-dispatch.
Pre-migration rows (NULL `executor_pid`) are fail-closed on the first
post-deploy restart (intended and acceptable).

NOTE: `os.kill(pid, 0)` carries a pid-recycle caveat — a recycled pid could
read as falsely-alive. A falsely-alive false-positive is acceptable relative
to the risk of duplicate runs from a false-negative.

#### Transitions

```
pending → (run_step pickup) → in_progress → { completed | failed | cancelled | in_progress(delegated) | in_progress(blocked_on_job) | escalated }

in_progress(delegated) → (all children terminal) → in_progress (re-entry, block_kind cleared on claim)
in_progress(blocked_on_job) → (all blocking jobs reach terminal state; _maybe_resume_blocked_task enqueues while the row stays in_progress) → in_progress (run_step CAS admits exactly one on pickup, clearing block_kind)
escalated → (POST /resolve-escalation continue) → pending (re-enqueued; manager's next prompt carries an ESCALATION RESOLVED header with the founder's rationale)
escalated → (POST /resolve-escalation cancel)  → cancelled (deliberate founder stop; notifies parent and kills owned jobs — parity with old reject path, but terminal status is CANCELLED not FAILED)
escalated | in_progress(delegated) → (revisit / thread-dispatch names it in lineage) → resolved_superseded (terminal; block_kind cleared, audit cites the continuation root task_id; NO re-enqueue. The delegated close is gated on all children being terminal and never cascade-SIGTERMs live siblings)
escalated → (POST /resolve-escalation continue on exhaustion escalation) → pending (re-enqueued; parent carries the exhaustion context + failure reason from the failed subtask — manager can re-ground and re-delegate)
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
in `escalated` for founder review (root tasks only; a non-root over-budget task fails and hands back to its parent). A second budget — `max_revise_rounds` (org_config, 0 = disabled) — caps the number of genuine revise cycles (worker-of-record re-delegations) per slice — i.e. a slice runs its initial attempt plus up to `max_revise_rounds` revises. Each revise increments `revision_count`; when `revision_count >= max_revise_rounds` the next genuine revise trips a DELIBERATE stop-with-best (best-effort partial preserved) that mirrors the step-budget terminal: non-root fails back to parent, root escalates. The stop is explicitly NOT auto-revisited.

#### External waits

The orchestrator does not gain new task states for external systems. External waits use jobs. A task that cannot continue until an external job finishes reports `status="blocked"` with non-empty `waiting_on_job_ids`; the row parks as `in_progress` with `block_kind='blocked_on_job'`; and the existing all-terminal job predicate resumes the task. The orchestrator never infers task completion from intermediate external signals such as submission, handoff, or an absent result.

Example: a PR CI / guarded merge helper is a bounded job that polls an external CI system and wakes the task through `blocked_on_job_ids`. The engineering-domain specifics live in the jobs skill and agent guides.

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

---

## 4. Runtime-Managed Skill Policy (CONTEXT/ADMISSION)

The runtime-managed skill policy is an agent **context/admission** mechanism
— it controls which skills appear in an agent session's compact skill index.
It is **explicitly NOT a permission layer**. Capability remains governed
ONLY by the existing permission model (§3). Skills do not grant tools,
credentials, network access, filesystem access, sandbox policy, or
permission-map/allow-rule/auth changes.

**Founder ruling (THR-055 seq 55):** The catalog-approval gate is REMOVED for
first-party HappyRanch skills. For first-party skills, runtime approval
duplicates the release pipeline — PR review + merge + deploy IS the approval.
Exposure is now: catalog-presence + status==enabled + eligibility-matched.
Runtime approval is DEFERRED to a future user-authored-skills feature and will
be re-introduced only if/when that audience ships.

### 4.1 Two-Gate Model

A skill reaches an agent session only when **both** gates pass:

1. **Catalog Gate** — the skill is present in the catalog and enabled.
   - `status` must be `enabled`.
   - Disabled skills are blocked.
   - There is NO approval gate — for first-party skills, the release pipeline
     (PR review + merge + deploy) IS the approval.

2. **Eligibility Gate** — org/team/agent policy makes the skill eligible.
   - Additive inheritance with explicit deny (`deny` wins over `allow`):
     ```
     effective = present_catalog
       ∩ (org.allow ∪ team.allow ∪ agent.allow)
       \ (org.deny ∪ team.deny ∪ agent.deny)
     ```
   - A disabled registry entry remains unavailable even if eligible.
   - Unknown skill ids in eligibility config produce validation warnings and
     are excluded from the session index.

### 4.2 Policy Classes

| Policy class | Governance |
| --- | --- |
| `standard_operational` | Workflow guidance, repo conventions, role playbooks, debugging aids (e.g., `review`). Passes the catalog gate with status=enabled. |
| `high_impact_policy` | Pricing, legal/compliance, security, production release, escalation thresholds, agent roster governance (e.g., ``manage-agent``, ``manage-repo``). Scoped to managers/operators via eligibility policy (`policy_class` still scopes eligibility). Passes the catalog gate with status=enabled (no per-version approval gate — release pipeline IS the approval). |
| `system_contract` | Runtime protocol and mandatory operating-contract skills (e.g., `start-task`, `thread`, `jobs`). **Outside the toggleable catalog** — not shown, not toggleable. |

### 4.3 Compact Session Skill INDEX

At session creation, HappyRanch injects a compact skill **index** into the
agent prompt — not full skill bodies. Each index line carries: `id`, `version`,
`description`, `when_to_use`, and `source` (the on-disk path to `SKILL.md`).
The agent loads the full skill body on demand through the executor's normal
skill-loading mechanism.

Format:
```
- hr:<slug>@<version> — <description>. <when_to_use> Load full instructions from <source>/SKILL.md.
```

The compact index is stable and deterministic for the same registry + config
inputs. Skills omitted by policy do not appear. Global CLI skills are untouched.

### 4.4 Admin Surface (CLI-first)

V1 provides CLI commands that read the file/YAML-backed registry + resolver +
exposure directly from disk (no daemon round-trip):

- `happyranch skills catalog list` — list all registered skills.
- `happyranch skills catalog validate` — validate registry entries and
  eligibility policy; surfaces unknown-id warnings and malformed skill.yaml
  entries.
- `happyranch skills effective --agent <name>` — show effective skills for an
  agent, with provenance (which scope+rule admitted/denied each skill).
- `happyranch skills policy explain <skill_id> --agent <name>` — explain why
  a skill is or isn't available, including both gate results and
  eligibility provenance.

Registry and eligibility mutations emit audit rows under the `config:skills`
scope prefix (matching the established `config:<section>` convention from
THR-035).

### 4.5 Fenced Non-Goals

The following are **explicitly out of scope** for the runtime-managed skill
policy:

- Skills **do not** grant tools, credentials, network access, filesystem
  access, sandbox policy, permission maps, allow-rule, or auth changes.
- System/contract skills are **not toggleable** — they are outside the catalog.
- **No SQLite migration** — v1 is file/YAML-backed only.
- **No web Settings UI** or marketplace in v1.
- **No executable/permission-bearing package surface** — v1 packages include
  `SKILL.md`, `skill.yaml`, and optional `references/` and `assets/`
  directories only.
- **No auth or permission-model change** — the existing executor-native
  sandboxing + system prompt guardrails remain the sole capability gate.

### 4.6 Session-Time Skill Freshness & Protocol Doc Injection (THR-070)

**Skill body freshness.** System/contract skill bodies are copied from the
bundled ``project_root/protocol/skills/`` into the agent workspace at
`ensure_workspace_ready` time (lifecycle events like init-agent,
set-executor). Before THR-070, live agents' on-disk skill bodies froze until
the next lifecycle event — an edit to a skill in the bundle would not reach a
running agent.

**Phase-4 cutover (THR-055).** The session-time wholesale refresh and the
bootstrap ``_copy_skills`` wholesale copy are BOTH gated behind the reversible
``_WHOLESALE_DUMP_ENABLED`` flag (default ``False`` in
``workspace_adapters.py``). The flag gates two code paths:
- **Session-time:** ``refresh_session_skills`` — called on every session
  creation to re-copy the bundled ``protocol/skills/`` tree into
  ``.claude/skills/`` and ``.agents/skills/``.
- **Bootstrap:** ``_copy_skills`` in the three executor adapters
  (``ClaudeWorkspaceAdapter``, ``CodexWorkspaceAdapter``,
  ``OpencodeWorkspaceAdapter``) — called from ``ensure_workspace_ready`` at
  lifecycle events (init-agent, set-executor).

When the flag is ``False`` (the cutover default), neither code path copies
skills. The explicit injection paths — ``inject_system_contracts`` (§4.7) and
``inject_managed_skills`` (§4.10) — are the SOLE skill-delivery mechanism.
The flag can be set to ``True`` for rollback to the legacy wholesale-dump
model without a code revert.

**Protocol doc manifest.** Protocol ``.md`` docs (the files in
``project_root/protocol/*.md``) are NEVER copied to agent workspaces. Instead,
a minimal one-line-per-doc **manifest** is injected into every session prompt
alongside the compact skill index. Each line carries the doc title, a one-line
purpose, and the absolute bundled path. Agents read full doc bodies on-demand
from the bundled path — no new tool, no new daemon route, no injection of full
bodies.

This replaces the legacy model where agents read protocol docs from the
``repos/happyranch/protocol/`` clone (which was fresh only at
once-per-session git-pull).

**Session-path coverage.** The four session-creation paths that inject the
manifest and refresh skills are:
1. ``Orchestrator._run_agent`` (task/subtask)
2. ``wake_runner.run_wake`` (working-hours wake)
3. ``thread_runner.run_invocation`` (thread reply/bootstrap)
4. ``dream_runner.run_dream`` (private dream)

**Hard constraints.** Skill refresh and manifest injection are additive only —
they do not modify ``resolve_managed_skills_index``, ``render_compact_skill_index``,
the permission model, executor skill-load paths, or the SQLite schema. No new
daemon routes are added.

### 4.7 System-Contract Injection (THR-055 Phase 1 + Phase 4)

System-contract skills — ``start-task``, ``jobs``, ``make-worktree``, ``thread``,
``dream`` — are mandatory operating-contract skills injected by the runtime based
on session/context type. They are defined in the single-source-of-truth module
``runtime/skills/system_contracts.py`` and are OUTSIDE the toggleable managed
catalog (they are NOT displayed by ``skills catalog list`` and are never
manager-toggleable).

**Injection model (Phase 4 — CUT OVER).** On EVERY session creation,
``inject_system_contracts`` and ``inject_managed_skills`` (see §4.10) are the
SOLE skill-injection paths. The wholesale ``protocol/skills/`` dump is DISABLED
through TWO code paths, both gated behind the reversible
``_WHOLESALE_DUMP_ENABLED = False`` flag in ``workspace_adapters.py``:

1. **Session-time** — ``refresh_session_skills`` (called on every session
   creation) is a no-op when the flag is ``False``.
2. **Bootstrap** — ``_copy_skills`` in the three executor adapters
   (Claude, Codex, Opencode), called from ``ensure_workspace_ready`` at
   lifecycle events, is a no-op when the flag is ``False``.

Both gates prevent the wholesale copy of ALL 8 ``protocol/skills/``
directories (including the 3 managed-catalog skills) into the workspace.
A freshly-bootstrapped workspace receives NO skills from the wholesale path;
skills are delivered exclusively through the explicit injection paths. The
completeness of this delivery model is proven by the contract-completeness
guard test in ``test_skill_cutover_completeness.py``.

**Phase 1 (historical).** The initial deployment ran ``inject_system_contracts``
ADDITIVELY alongside the wholesale dump. This was the safety net proved correct
in the guard test, then removed in Phase 4.

**Context-exposure predicates** (``SessionContext`` enum):

| Contract | TASK | THREAD | WAKE | DREAM | Requires repos? |
| --- | :---: | :---: | :---: | :---: | :---: |
| ``start-task`` | ✓ | | ✓ | | no |
| ``jobs`` | ✓ | ✓ | ✓ | ✓ | no |
| ``make-worktree`` | ✓ | ✓ | ✓ | ✓ | yes |
| ``thread`` | ✓ | ✓ | ✓ | | no |
| ``dream`` | | | | ✓ | no |

**Session-context mapping:**
- ``TASK`` — ``Orchestrator._run_agent`` (ordinary task/subtask session)
- ``THREAD`` — ``thread_runner.run_invocation`` (thread reply/bootstrap)
- ``WAKE`` — ``wake_runner.run_wake`` (working-hours wake / task-followup)
- ``DREAM`` — ``dream_runner.run_dream`` (scheduled dream)

**Repo-capability check.** ``make-worktree`` is gated on the agent workspace
having at least one cloned git repository under ``repos/``. Agents with no
repo write surface never receive ``make-worktree``.

**Debug visibility.** ``happyranch skills effective --agent <name>`` displays
a distinct "System Contracts (runtime-injected)" section separate from managed
catalog skills. The optional ``--context`` flag filters the display by session
context; ``--workspace`` enables the repo check.

**Fences.** System-contract injection does not:
- Grant tools, credentials, or capabilities (skills are permission-inert)
- Modify the managed catalog, registry, or eligibility resolver
- Require a SQLite migration (file/YAML-backed only)
- Add new daemon routes
- Change the existing permission model

### 4.8 Managed-Catalog Standard-Operational Entry — ``review`` (THR-055 Phase 2)

The ``review`` skill is the first HappyRanch skill migrated into the managed
catalog as a ``standard_operational`` entry. It was previously delivered via
the wholesale ``protocol/skills/`` dump alongside the system contracts.

**Package location.** ``runtime/skills/review/{skill.yaml,SKILL.md}``.

**Registration metadata.**
- ``id``: ``hr:review``
- ``policy_class``: ``standard_operational``
- ``owner``: ``engineering_manager``
- ``version``: ``1.0.0``

**Eligibility scoping.** ``review`` visibility is scoped to **team managers and
review-loop participants** — NOT org-wide. The default eligibility policy in
``org/config.yaml`` grants access to:
- The ``engineering`` team (dev_agent, code_reviewer, qa_engineer,
  engineering_manager).
- ``product_lead`` via agent-scoped allow (team manager outside the
  engineering team who participates in founder review loops).

A non-participant agent (e.g., ``support_agent`` on the ``cx`` team, or any
agent outside these allow lists) does NOT resolve ``review`` as exposed.
The eligibility formula is the standard additive-inheritance model (see §4.1):
team-scoped allow, with agent-scoped allow for team managers outside the
engineering team.

**Provenance.** ``skills effective --agent dev_agent`` shows ``review`` with
``team(engineering) ALLOW`` eligibility provenance and ``standard_operational``
policy class. ``skills policy explain hr:review --agent dev_agent`` shows the
catalog gate (PASS — present, enabled) and eligibility gate (team-scoped allow).

**Phase-2 additive constraint.** The ``review`` SKILL.md body also remains
in ``protocol/skills/review/`` so that the existing wholesale-dump path
(``refresh_session_skills``) continues to deliver ``review`` to all agents
as a safety net. Physical removal from the always-injected set is a Phase-4
change gated on a completeness test proving catalog resolution delivers the
full required set. Phase 2 is ADDITIVE only — the managed-catalog entry is
registered and eligibility is scoped; the wholesale dump is untouched.

**Fences.** Phase 2 does not:
- Grant tools, credentials, or capabilities (review command access remains
  in allow_rules / daemon auth per the existing permission model)
- Physically delete ``review`` from ``protocol/skills/`` (Phase 4)
- Require a SQLite migration (file/YAML-backed only)
- Add new daemon routes
- Change the existing permission model or auth

### 4.9 Managed-Catalog High-Impact Entries — ``manage-agent`` + ``manage-repo`` (THR-055 Phase 3)

The ``manage-agent`` and ``manage-repo`` skills are registered as
``high_impact_policy`` managed-catalog entries. They govern agent roster
management (enroll/update/terminate agents) and agent workspace repository
configuration (add/remove/update repos).

**Package locations.**
- ``runtime/skills/manage-agent/{skill.yaml,SKILL.md}``
- ``runtime/skills/manage-repo/{skill.yaml,SKILL.md}``

**Registration metadata.**
- ``hr:manage-agent`` and ``hr:manage-repo``
- ``policy_class``: ``high_impact_policy``
- ``owner``: ``engineering_manager``
- ``version``: ``1.0.0``

**Eligibility-scoped exposure.** ``manage-agent`` and ``manage-repo`` visibility
is governed EXCLUSIVELY by the two-gate model (§4.1): catalog-presence +
status==enabled + eligibility-matched. There is NO per-version approval gate —
for first-party skills, the release pipeline (PR review + merge + deploy) IS the
approval. An eligible manager/operator resolves them as exposed; a non-eligible
agent does not.

Any future approval concept (for user-authored or third-party skills) would be a
PLATFORM-OWNER catalog-admission gate — not a second-stage gate within the
first-party release pipeline and not a customer-self-serve feature.

**Eligibility scoping.** ``manage-agent`` and ``manage-repo`` visibility is
scoped to **MANAGER/OPERATOR agents** — NOT org-wide. The default eligibility
policy in ``org/config.yaml`` grants access to:
- ``engineering_manager`` via agent-scoped allow (engineering team manager).
- ``product_lead`` via agent-scoped allow (product team manager).

Non-manager agents (including engineering team workers such as ``dev_agent``,
``code_reviewer``, ``qa_engineer``) do NOT resolve ``manage-agent`` or
``manage-repo`` as exposed — even if they are in the engineering team. The
eligibility formula is the standard additive-inheritance model (see §4.1):
agent-scoped allow only; no team or org scope.

**HIGH-IMPACT POLICY = GUIDANCE VISIBILITY + VERSION PROVENANCE ONLY — NOT
COMMAND ACCESS.** ``high_impact_policy`` governs guidance visibility + version
provenance in the compact skill index. It does NOT grant or deny command
execution. ``manage-agent`` and ``manage-repo`` command access remains
separately governed by allow_rules / daemon auth per the existing permission
model (§3). The policy model is additive and permission-inert.

**Phase-3 additive constraint.** The ``manage-agent`` and ``manage-repo``
SKILL.md bodies also remain in ``protocol/skills/manage-agent/`` and
``protocol/skills/manage-repo/`` so that the existing wholesale-dump path
(``refresh_session_skills``) continues to deliver them to all agents as a
safety net. Physical removal from the always-injected set is a Phase-4
change gated on a completeness test proving catalog resolution delivers the
full required set. Phase 3 is ADDITIVE only — the managed-catalog entries are
registered and eligibility is scoped; the wholesale dump is untouched.

**Phase-4 cutover (COMPLETED).** The wholesale ``protocol/skills/`` dump is
disabled through both paths — session-time ``refresh_session_skills`` and
bootstrap ``_copy_skills`` in the three executor adapters — gated behind
``_WHOLESALE_DUMP_ENABLED = False``. The 8 ``protocol/skills/`` directories
remain on disk as a packaged safety net (re-enable with the flag) but are
no longer copied into workspaces. The ``SKILL.md`` source of truth for the
3 managed-catalog skills lives in ``runtime/skills/<id>/``. See §4.6 and
§4.10 for the full delivery model.

**Fences.** Phase 3 does not:
- Grant tools, credentials, or capabilities (manage-agent/manage-repo command
  access remains in allow_rules / daemon auth per the existing permission model)
- Require a SQLite migration (file/YAML-backed only)
- Add new daemon routes
- Change the existing permission model or auth
- Add a web admin UI
- Record any founder approval for the version (maker-checker — founder action only)

### 4.10 Phase-4 Cutover — Managed-Skill Workspace Injection (THR-055 Phase 4)

The Phase-4 cutover completes the migration by STOPPING the wholesale
``protocol/skills/`` dump and delivering skills EXCLUSIVELY through:
1. ``inject_system_contracts`` — context-aware system-contract injection (§4.7)
2. ``inject_managed_skills`` — policy-resolved managed-catalog injection (this section)

**Injection model.** On EVERY session creation (task/subtask, thread reply,
wake, dream), ``inject_managed_skills`` resolves the two-gated catalog +
eligibility policy for the session's (agent, team) and copies each EXPOSED
managed skill from ``runtime/skills/<id>/`` into ``.claude/skills/<id>/``
and ``.agents/skills/<id>/``.

**Resolution flow:**
1. Load ``SkillRegistry`` from ``<project_root>/runtime/skills/``.
2. Load eligibility policy from ``<project_root>/org/config.yaml``
   (``skills`` section).
3. Resolve exposed skills via ``resolve_exposed_skills`` (both gates:
   catalog gate + eligibility gate).
4. Copy each exposed skill's package into the workspace skill dirs.

**Context-exposure rules:** managed skills are context-AGNOSTIC — ``review``,
``manage-agent``, and ``manage-repo`` are injected into ALL session types
where the agent is eligible ($4.1 two-gate model). System contracts remain
context-aware ($4.7).

**Fail-closed.** Disabled, catalog-absent, or ineligible skills are NOT
injected. The catalog gate (presence + enabled) is independent of the
eligibility gate — both must pass.

**Reversible gate.** The wholesale dump is disabled by default
(``_WHOLESALE_DUMP_ENABLED = False`` in ``workspace_adapters.py``). This
gates TWO code paths — the session-time ``refresh_session_skills`` and the
bootstrap-time ``_copy_skills`` in the three executor adapters
(Claude, Codex, Opencode). Setting the flag to ``True`` re-enables the
legacy wholesale dump through both paths without a code revert.

The ``protocol/skills/`` directories remain on disk as packaged source
material for the system-contract injection path and as a reversion safety
net. They are NOT deleted — only the copy-into-workspace step is gated.

**Coverage.** The contract-completeness guard test
(``test_skill_cutover_completeness.py``) proves that every agent (7) × every
session context (4) × every repo state (2) = 56 combinations receive the
complete required set without the wholesale dump. The test asserts:
- System contracts are context-correct per §4.7 predicates.
- ``review`` is injected for engineering team + product_lead.
- ``manage-agent`` / ``manage-repo`` are exposed to eligible managers/operators
  only and hidden from non-eligible agents (eligibility gate).
- ``dream`` is excluded from non-dream contexts.
- ``make-worktree`` is repo-gated.

**Session-path coverage.** ``inject_managed_skills`` is wired into all 4
session-creation callers:
1. ``Orchestrator._run_agent`` (task/subtask) — resolves team via
   ``load_agent``.
2. ``thread_runner.run_invocation`` (thread reply/bootstrap) — resolves
   team from ``ThreadParticipant`` record.
3. ``wake_runner.run_wake`` (working-hours wake) — resolves team from
   ``agent_def``.
4. ``dream_runner.run_dream`` (private dream) — resolves team via
   ``load_agent``.

**Fences.** Phase 4 does not:
- Grant tools, credentials, or capabilities (skills are permission-inert)
- Modify the permission model, auth, allow_rules, or daemon authorization
- Require a SQLite migration (file/YAML-backed only)
- Add new daemon routes
- Add a web admin UI
- Delete ``protocol/skills/`` directories (reversible via flag)
