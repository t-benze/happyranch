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

Failure-join reuses bounded failure-recovery (§Failure-recovery contract):
failed fan-out children individually consume re-spawn rounds; the parent wakes
on each terminal child, and exhaustion escalates the parent after
`_FAILURE_ROUND_BOUND` (2) failed children. For a pipeline carrier this is
**fail-closed at the carrier**: a leg verdict-mismatch or a failed leg fails
the whole carrier (no partial-chain completion), and the failed carrier then
feeds the parent's barrier exactly as any failed child does. No partial-join
or cascade-fail semantics are introduced.

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
— it controls which approved skills appear in an agent session's compact skill
index. It is **explicitly NOT a permission layer**. Capability remains
governed ONLY by the existing permission model (§3). Skills do not grant
tools, credentials, network access, filesystem access, sandbox policy, or
permission-map/allow-rule/auth changes.

### 4.1 Two-Gate Model

A skill reaches an agent session only when **both** gates pass:

1. **Catalog Gate** — the registry entry is approved for catalog use.
   - `approval_state` must be `approved`.
   - `status` must be `enabled`.
   - **Founder ruling (THR-055 seq 17):** `high_impact_policy` skills require
     founder or designated-owner approval before catalog admission AND before
     EACH version upgrade. Approval is version-specific — approval of `1.0.0`
     does not imply approval of `1.1.0`. Upgrading a `high_impact_policy`
     skill returns it to `pending_review` / unavailable until the new version
     is approved.
   - `draft`, `pending_review`, `rejected`, `deprecated`, or missing approval
     metadata blocks the catalog gate.

2. **Eligibility Gate** — org/team/agent policy makes the skill eligible.
   - Additive inheritance with explicit deny (`deny` wins over `allow`):
     ```
     effective = approved_catalog
       ∩ (org.allow ∪ team.allow ∪ agent.allow)
       \ (org.deny ∪ team.deny ∪ agent.deny)
     ```
   - An unapproved skill remains unavailable even if eligibility allows it.
   - A disabled registry entry remains unavailable even if approved and eligible.
   - Unknown skill ids in eligibility config produce validation warnings and
     are excluded from the session index.

### 4.2 Policy Classes

| Policy class | Governance |
| --- | --- |
| `standard_operational` | Workflow guidance, repo conventions, role playbooks, debugging aids. Owner or team manager may approve. |
| `high_impact_policy` | Pricing, legal/compliance, security, production release, escalation thresholds. Founder or designated-owner approval required for catalog admission AND each version upgrade. |
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
  a skill is or isn't available, including both gate results, approval
  records, and eligibility provenance.

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

Now, the skill tree is **refreshed on EVERY session creation** (task/subtask,
thread reply, wake, dream) by a shared idempotent helper that re-copies from
the bundled source into both ``.claude/skills/`` and ``.agents/skills/``. The
source is always ``_resolve_skills_src(settings)`` = ``project_root/protocol/skills/``
(the bundled runtime), never a workspace clone or stale frozen copy.

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
