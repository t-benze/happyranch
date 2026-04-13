# Orchestrator: Routing, Permissions & State

The custom application layer that sits above CrewAI — task routing, inter-crew communication, performance tiers, permissions, and the task state machine.

---

## 1. Orchestrator Responsibilities

The orchestrator is the glue that sits above CrewAI. It is NOT a CrewAI concept — it's your application code.

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
│  │Inter-Crew │  │ Knowledge│  │   Founder     │  │
│  │  Comms    │  │   Base   │  │  Dashboard    │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │         Agent Executor Abstraction        │    │
│  │  Claude Code │ Codex │ OpenCode │ Native  │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
        │              │              │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Content  │   │ Product │   │   Ops   │   │   CX    │
   │  Crew    │   │  Crew   │   │  Crew   │   │  Crew   │
   └─────────┘   └─────────┘   └─────────┘   └─────────┘
```

### What the orchestrator does

**1. Receives work requests** and routes them to the right Crew. A new content brief goes to Content Crew. A partner application goes to Ops Crew. A bug report goes to the Product & Engineering Crew.

**2. Manages inter-Crew communication.** When the Content Crew publishes a guide, it notifies the CX Crew so Support Agent knows about new content. When the Product & Engineering Crew changes a payment flow, it triggers a cross-audit task in the Ops Crew. These are not internal to any one Crew — the orchestrator handles the handoff.

**3. Runs the escalation router.** When an agent calls the `escalate` tool, the orchestrator evaluates the 12 escalation rules (from `04-escalation-rules.md`) and either routes to the relevant manager's Crew or sends a notification to the founder.

**4. Manages the revision loop.** When QA returns REVISE, the orchestrator tracks the revision count and either re-triggers the Content Crew with feedback or escalates after max rounds.

**5. Runs post-execution scoring.** After each Crew run completes, the orchestrator extracts completion reports, QA verdicts, and revision history, then updates the agent scorecards. It adjusts the next Crew run's configuration based on performance tiers (e.g., adding extra review steps for yellow/red agents).

**6. Assembles agent context.** Before each session, the orchestrator gathers the system prompt, learnings file, scorecard, team health, and task-specific context, then writes them into the agent's workspace in the format expected by the configured executor.

**7. Provides the founder dashboard.** Aggregates audit logs, escalation summaries, agent scorecards, and team health metrics into a weekly report.

### Inter-Crew communication patterns

| Trigger | From Crew | To Crew | Payload |
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
- Instantiates the 4 Crews with their agents and task templates
- Exposes an API (or CLI) for submitting work requests
- Maintains state in a database (SQLite for prototype, PostgreSQL for production)
- Runs agent sessions via the executor abstraction (not all running simultaneously)
- Listens for escalation signals and inter-crew communication
- Persists audit logs, scorecards, and agent memory

---

## 2. Performance Tier Impact on Crew Configuration

The orchestrator dynamically adjusts how Crews run based on agent performance tiers.

### Green tier (>90% acceptance)
- Standard flow: task → agent executes → next step
- Minimal supervision from manager agent

### Yellow tier (75-90%)
- Manager agent reviews ALL output before it proceeds
- In CrewAI terms: add an explicit `manager_review` Task after the agent's Task

### Red tier (<75%)
- Double review: supervisor + peer manager
- In CrewAI terms: add TWO review Tasks — one for the supervising manager, one for the peer-audit manager (from a different Crew, routed by the orchestrator)
- Agent scope reduced: only assigned simpler task variants
- Founder receives weekly performance report for this agent

### How this works in practice
The orchestrator maintains a scorecard per agent. Before kicking off a Crew run, it checks relevant agent tiers and adjusts the Task chain:

```
Standard (Green):  write_content → qa_review → done
Yellow Writer:     write_content → manager_pre_review → qa_review → done
Red Writer:        write_content → manager_pre_review → peer_review → qa_review → done
```

The Crew is instantiated with the appropriate Task list each time. This is not something CrewAI handles automatically — your orchestrator builds the Task list dynamically.

---

## 3. Permission and Authority Model

### Approach: Claude Code native permissions + system prompt guardrails

All agents run with `claude --permission-mode auto`. Permissions are generous — agents can read, write, and execute freely within their workspace. The `.claude/settings.json` in each workspace auto-allows all standard tools.

**Founder-concern boundaries** (the only things that truly need restricting) are enforced through two layers:

1. **System prompt** — each agent's `CLAUDE.md` explicitly states what it cannot do. The agent is instructed to call `escalate()` when it encounters these boundaries.
2. **Orchestrator post-session review** — the orchestrator inspects completion reports and audit logs for violations. If an agent somehow bypasses its system prompt instructions, the orchestrator catches it and escalates.

This approach avoids building a complex custom permission layer. Claude Code's `--permission-mode auto` eliminates approval friction, while the system prompt provides the "soft" guardrails and the orchestrator provides the "hard" backstop.

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
**Orchestrator action**: Routes the escalation per the 12 rules in `04-escalation-rules.md`. Creates a founder notification with the agent's summary and recommendation. Holds the specific blocked step (not the entire Crew).
**Resolution**: Founder approves or denies via the dashboard. Orchestrator resumes the task with the decision injected into context.

#### Type 3: Needs another agent's work
**What**: The task has a cross-agent or cross-crew dependency.
**Example**: Payment Agent proposes a payment flow change, but it needs Compliance Agent review before Engineering Head can approve. Dev Agent needs to implement a feature that requires a partner API endpoint, but Partner Liaison hasn't onboarded that partner yet.
**Response**: Agent identifies the dependency in its completion report: "Blocked on: Compliance Agent cross-audit of this payment flow change. Cannot proceed until PCI-DSS and cross-border compliance is verified."
**Task state**: Moves to `blocked_on_dependency`.
**Orchestrator action**: Reads the dependency from the completion report. Spawns the required agent session (Compliance Agent in the Ops Crew) with the dependency context. The blocking task is queued, not abandoned.
**Resolution**: Once the dependency agent completes its work, the orchestrator resumes the blocked task with the dependency result injected into context.

#### Type 4: Ambiguous or novel situation
**What**: Agent encounters something not covered by existing permissions or SOPs.
**Example**: QA Agent discovers content that might be politically sensitive but isn't sure. Compliance Agent finds a regulation that could be interpreted two ways.
**Response**: Agent calls `escalate(category="novel", severity="medium", summary="...")` with its best assessment and a recommendation.
**Task state**: Moves to `waiting_for_guidance`.
**Orchestrator action**: Routes to founder. The agent's recommendation is included so the founder can often just approve/deny rather than research from scratch.
**Resolution**: Founder's decision is logged as precedent and added to the knowledge base so future occurrences are handled automatically.

### Task state machine

```
                    ┌──────────┐
                    │ PENDING  │
                    └────┬─────┘
                         │ assigned to agent
                    ┌────▼─────┐
                    │IN_PROGRESS│
                    └────┬─────┘
                         │
              ┌──────────┼──────────────┐
              │          │              │
     ┌────────▼───┐ ┌───▼────┐ ┌──────▼──────────┐
     │ WAITING_FOR│ │BLOCKED │ │   COMPLETED     │
     │ _APPROVAL  │ │_ON_DEP │ │  (with report)  │
     └────────┬───┘ └───┬────┘ └──────┬──────────┘
              │         │             │
              │ approved│ dep resolved│
              │         │             │
     ┌────────▼─────────▼──┐   ┌─────▼──────┐
     │   RESUMED           │   │  IN_REVIEW  │
     │ (re-enters agent    │   │ (supervisor)│
     │  with new context)  │   └─────┬──────┘
     └─────────┬───────────┘         │
               │                ┌────┼─────────┐
               └──► loops back │    │          │
                    to         │    │          │
                 IN_PROGRESS ┌─▼──┐ ▼    ┌────▼────┐
                             │PASS│ │    │ REVISE  │
                             └──┬─┘ │    └────┬────┘
                                │   │         │ (back to
                         ┌──────▼┐  │         │ IN_PROGRESS,
                         │APPROVED│  │         │ max 2 rounds)
                         └───────┘  │         │
                                ┌───▼───┐     │
                                │REJECT │     │
                                └───┬───┘     │
                                    │         │
                              ┌─────▼─────┐   │
                              │ ESCALATED │◄──┘ (after max rounds)
                              └───────────┘
```

### Timeout handling

Blocked tasks don't wait forever:

| Block type | Default timeout | On timeout |
|---|---|---|
| Waiting for founder approval | 24 hours | Re-notify founder + flag in dashboard as urgent |
| Blocked on dependency (same crew) | 2 hours | Escalate to manager agent |
| Blocked on dependency (cross-crew) | 4 hours | Escalate to both managers |
| Waiting for guidance (novel situation) | 24 hours | Re-notify founder + agent proceeds with conservative default if one exists |

The orchestrator runs a background check every 15 minutes for timed-out tasks. If a founder approval times out twice (48 hours total), the task is flagged as critical on the dashboard and the orchestrator sends a notification through whatever channel the founder has configured (email, Feishu, SMS).

### Permission evolution

Permissions aren't static. As the system matures:
- Agents with sustained Green tier may earn expanded scope (orchestrator widens their permission config)
- Red tier agents get scope *reduced* — fewer allowed directories, restricted shell access, simpler tasks only
- Novel situations that the founder resolves become codified rules — the orchestrator updates the permission config and knowledge base so that situation is handled automatically next time
- The founder can adjust any agent's permissions at any time via the dashboard
