# CrewAI Implementation Blueprint

## How CrewAI Maps to the Tourism Organization

This document is the architectural bridge between the org design (charter, system prompts, escalation rules) and a working CrewAI implementation. No code — just the blueprint.

---

## 1. Concept Mapping

| Your Org Concept | CrewAI Concept | Notes |
|---|---|---|
| Worker Agent (e.g., Content Writer) | `Agent` | Direct 1:1 mapping. System prompt → `backstory` |
| Manager Agent (e.g., Content Manager) | `Agent` used as `manager_agent` in a Crew | CrewAI's hierarchical process supports this natively |
| Content task (e.g., "write Macau visa guide") | `Task` | One Task per discrete unit of work |
| QA review of that content | Separate `Task` assigned to QA Agent | Maker-checker preserved — different agent, different task |
| Manager approval step | Handled by `hierarchical` process | Manager agent reviews final output before returning |
| Functional team (Content Writer + QA + Content Mgr) | `Crew` | One Crew per manager domain |
| Peer audit (cross-manager review) | Custom — inter-Crew callback | Not native to CrewAI; built in the orchestrator layer |
| Escalation to founder | Custom — agent tool or callback | Agent calls an escalation tool; orchestrator routes it |
| Knowledge base | Shared RAG tool on agents | Each agent gets KB tool with appropriate read/write scope |
| Audit logger | Crew callback + custom tool | Hook into CrewAI's task callbacks |
| Performance scoring | Post-run wrapper | Score agents after each Crew execution |

---

## 2. Agent Execution Model

### Every agent runs as a coding-agent session

Each agent in the organization is not just an LLM call — it's a full coding-agent session that can read files, write files, run commands, search the web, and interact with APIs. The CrewAI layer orchestrates *when* each session runs, *what context* it gets, and *how* outputs flow between them.

### Provider-agnostic agent executor

The system supports multiple coding-agent backends. Each agent's configuration specifies which executor to use:

```
agent_config:
  content_writer:
    executor: claude-code      # or "codex", "opencode", "crewai-native"
    system_prompt: 03-system-prompts-workers.md#content-writer
    workspace: workspaces/content_writer/
    context_files:
      - 01-org-charter.md
      - knowledge_base/style_decisions.md
      - agent_memory/content_writer/learnings.md
      - agent_memory/content_writer/scorecard.md
```

Supported executors:

| Executor | How context is loaded | Best for |
|---|---|---|
| `claude-code` | Writes `CLAUDE.md` in agent workspace | Complex multi-file tasks, repo-aware work |
| `codex` | Writes `AGENTS.md` or passes via CLI args | Code-heavy tasks, OpenAI ecosystem |
| `opencode` | Writes config in its native format | Open-source flexibility, custom LLM backends |
| `crewai-native` | Injects as system message to LLM | Lightweight decisions, simple reviews, routing |

The orchestrator translates the agent config into the right format for whichever executor is configured. Swapping an agent from one executor to another is a one-line config change.

### When to use full coding-agent sessions vs. lightweight LLM calls

Not every task needs a full session. The config supports `crewai-native` for simpler tasks:

| Use full coding-agent session | Use lightweight crewai-native |
|---|---|
| QA Agent verifying links and prices live | Content Manager approving a QA-passed piece |
| Dev Agent implementing a feature | SEO Agent generating meta descriptions |
| Compliance Agent researching regulations | Manager agents making routing decisions |
| Support Agent querying booking database | Simple pass/fail review decisions |

Agents can even switch executors per task type — the Dev Agent uses a full session for feature work but crewai-native for triaging a bug report.

---

## 3. Agent Memory Architecture

### Problem
Coding-agent sessions are stateless — context is lost when a session ends. Agents need to remember past work and learn from experience across sessions.

### Solution: file-based memory (provider-agnostic)

Every agent has a persistent workspace directory. Before each session starts, the orchestrator assembles context files into that workspace. When the session ends, the orchestrator extracts learnings and writes them back.

```
agent_memory/
├── content_writer/
│   ├── learnings.md            # Accumulated operational knowledge
│   ├── scorecard.md            # Current performance metrics
│   └── recent_tasks.md         # Summary of last N tasks and outcomes
├── qa_agent/
│   ├── learnings.md
│   ├── scorecard.md
│   ├── known_issues.md         # Patterns of errors caught repeatedly
│   └── recent_tasks.md
├── content_manager/
│   ├── learnings.md
│   ├── scorecard.md
│   └── recent_tasks.md
└── ...
```

### Three layers of memory

**1. Institutional memory (knowledge base)**
Shared across all agents. Org charter, SOPs, brand guidelines, partner directory, regulatory summaries. Read-only for most agents, write access scoped per role.

**2. Agent-specific memory (learnings file)**
Each agent accumulates its own operational learnings. The QA Agent records "DSAL website is more reliable than MGTO for Macau visa info." The Content Writer records "always include UnionPay as a payment option for mainland content." These files persist across sessions and are loaded as context at session start.

After each task, the orchestrator prompts the agent: "Based on this task, are there any new learnings to record?" Responses are appended to the learnings file. Over time, when the file gets long, the orchestrator periodically asks the agent to consolidate and prune it.

**3. Performance memory (scorecard)**
The performance tracker generates a scorecard summary after each task. This is injected into the agent's context so it's aware of its own track record: "Your QA first-pass rate this month is 78% — below the 80% target. Currency conversion accuracy has been your weakest area." This drives self-correction without code changes.

### How context gets assembled at session start

The orchestrator builds a context bundle for each agent session:

```
1. System prompt (from 02/03-system-prompts-*.md)
2. Org charter summary (from 01-org-charter.md — key sections only)
3. Agent learnings file (from agent_memory/{agent}/learnings.md)
4. Current scorecard (from agent_memory/{agent}/scorecard.md)
5. Team health summary (generated by orchestrator)
6. Task-specific context (brief, prior drafts, QA feedback, etc.)
```

This bundle is written to the agent's workspace directory in whatever format the executor expects — `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, native config for OpenCode, or system message for crewai-native.

### Write-back protocol

After each session completes, the orchestrator:
1. Extracts the completion report (mandatory output format)
2. Checks for new learnings and appends to the learnings file
3. Updates the scorecard based on task outcome
4. Logs everything to the audit trail
5. Cleans up the agent workspace for the next session

---

## 4. Agent Lifecycle and Scheduling

### Principle: agents are not always running
Agents are not persistent processes. Running 12 agent sessions continuously would burn LLM credits and produce nothing — most agents are idle most of the time. Instead, the orchestrator manages agent lifecycles: spinning up sessions when there's work, and tearing them down when the task is done.

### Three operating modes

#### Mode 1: On-demand (most agents, most tasks)
The orchestrator spins up an agent session only when a task is assigned. The session starts, the agent completes the task, submits its completion report, and the session ends. Between tasks, the agent does not exist as a running process.

**Lifecycle:**
```
Task arrives in queue
    │
    ▼
Orchestrator assembles context (system prompt, learnings, scorecard, task brief)
    │
    ▼
Orchestrator spawns agent session (via configured executor)
    │
    ▼
Agent works on task (minutes, not hours)
    │
    ▼
Agent submits completion report
    │
    ▼
Orchestrator extracts output, logs results, writes back learnings
    │
    ▼
Session terminates — agent no longer running
```

**Typical session duration:** 1-5 minutes for most tasks. Complex tasks (Dev Agent implementing a feature, Compliance Agent running a full audit) may take 10-30 minutes.

**Which agents use this mode:** Content Writer, QA Agent, SEO Agent, Dev Agent, Payment Agent, Partner Liaison, Compliance Agent, and all 4 Manager Agents for their review/approval tasks.

#### Mode 2: Scheduled (recurring tasks on a cron)
Some work happens on a fixed schedule. The orchestrator's scheduler triggers these sessions at configured times. The session runs, completes its task, and shuts down — same as on-demand, but the trigger is a clock instead of a task queue.

**Scheduled tasks:**

| Schedule | Agent | Task |
|---|---|---|
| Daily 9:00 AM | Content Manager | Generate and send daily report to founder via Feishu |
| Daily 9:15 AM | Product Manager | Generate and send daily report |
| Daily 9:30 AM | Ops Manager | Generate and send daily report |
| Daily 9:45 AM | CX Manager | Generate and send daily report |
| Every Friday | QA Agent | Content freshness audit — flag guides older than 90 days |
| Every Monday | SEO Agent | Weekly keyword ranking report |
| 1st of month | Compliance Agent | Monthly regulatory scan across 3 jurisdictions |
| 1st of month | Ops Manager | Monthly partner SLA compliance review |
| Weekly Monday 10:00 AM | Orchestrator (not an agent) | Generate and post weekly org summary to Feishu |

Each scheduled task is configured in the orchestrator's scheduler (a cron-like system). Missed runs (e.g., Mac Mini was off) are handled by a catch-up mechanism: on startup, the orchestrator checks for missed scheduled tasks and runs them.

#### Mode 3: Persistent (Support Agent only)
The Support Agent is the one exception. Tourists need real-time help and the response time target is under 5 minutes. Two approaches:

**Option A: True persistent session.** The Support Agent runs as a long-lived agent session that waits for incoming inquiries. Advantages: instant response, no cold start. Disadvantages: continuous LLM session cost, needs health monitoring and auto-restart.

**Option B: Fast on-demand with warm-up.** The Support Agent is spun up on-demand like other agents, but with optimizations to reduce cold start: pre-assembled context kept ready, lightweight executor (crewai-native) for simple queries, full executor only for complex ones. If 10-20 second startup is acceptable within the 5-minute response window, this avoids the cost of a persistent session.

**Recommendation:** Start with Option B (fast on-demand). Switch to Option A only if response time is consistently too slow or if support volume justifies the cost.

### Concurrency

The orchestrator controls how many agent sessions run simultaneously. On a Mac Mini, practical limits:

| Constraint | Guideline |
|---|---|
| Concurrent sessions | 2-3 max (LLM API rate limits, memory, CPU for executors) |
| Task queuing | Tasks beyond concurrency limit are queued and processed FIFO |
| Priority queue | Tier 1 escalations and founder-initiated tasks jump the queue |
| Session timeout | 30 minutes max — if an agent session hasn't completed, kill it and escalate |

This means if the Content Writer is drafting a guide and the QA Agent needs to review something else simultaneously, both can run. But if a third task arrives, it waits in the queue. The orchestrator logs queue wait times — if tasks are regularly waiting, it's a signal to either optimize agent session speed or increase concurrency.

### Cost profile

With on-demand sessions, daily cost scales with actual work, not idle time:

| Phase | Estimated daily sessions | Estimated daily LLM cost |
|---|---|---|
| Phase 1 (Content Crew only) | 5-10 sessions | $3-8 |
| Phase 2 (+ Product/Ops Crews) | 15-25 sessions | $8-20 |
| Full org (all 4 Crews active) | 25-40 sessions | $15-35 |

These are rough estimates assuming Claude Sonnet pricing. Actual costs depend on task complexity, revision rounds, and which executor is used. The dashboard's cost tracking page (Page 6) gives you real-time visibility.

---

## 5. Crew Definitions

### Crew 1: Content Crew

**Process**: `hierarchical`
**Manager Agent**: Content Manager
**Worker Agents**: Content Writer, SEO Agent, QA Agent

**Typical task flow**:
1. Content Manager receives a brief (from editorial calendar or CX feedback)
2. Content Manager delegates writing task to Content Writer
3. Content Writer produces draft with completion report
4. Content Manager routes draft to QA Agent for review
5. QA Agent returns verdict: PASS / REVISE / REJECT
6. If REVISE → back to Content Writer with specific issues (max 2 rounds, then escalate)
7. If REJECT → Content Manager escalates to founder
8. If PASS → Content Manager makes final approval decision
9. SEO Agent reviews metadata/schema for approved content (parallel or post-approval)

**Tasks defined for this Crew**:

- `write_content`: Assigned to Content Writer. Input = brief + content type + audience. Output = draft with sources cited and completion report.
- `qa_review`: Assigned to QA Agent. Input = draft from write_content (via `context`). Output = structured verdict with checklist, issues, suggestions, and completion report.
- `seo_optimize`: Assigned to SEO Agent. Input = approved content. Output = title tag, meta description, schema markup recommendations, internal linking suggestions.
- `manager_review`: Implicit in hierarchical process — Content Manager reviews the chain output and issues final decision.

**Revision loop**: CrewAI doesn't have native loops. Two approaches:
- **Option A (simpler)**: Define `write_content` and `qa_review` as a sequence. If QA returns REVISE, the orchestrator creates a new Crew run with the revision feedback injected into the brief. Track revision count externally.
- **Option B (within CrewAI)**: Use a `callback` on the `qa_review` task that inspects the verdict and conditionally spawns a new `write_content` task. Requires custom callback logic.

**Recommendation**: Option A — cleaner separation, easier to audit, revision count tracked in your external state.

---

### Crew 2: Product Crew

**Process**: `hierarchical`
**Manager Agent**: Product Manager
**Worker Agents**: Dev Agent, Payment Agent

**Typical task flows**:

- *Feature development*: Product Manager defines spec → Dev Agent implements → Product Manager reviews → deploy
- *Payment flow change*: Payment Agent proposes change → Compliance Agent cross-audits (via inter-crew call) → Product Manager approves → Dev Agent implements
- *Bug fix*: Product Manager triages → Dev Agent fixes → Product Manager verifies

**Tasks defined for this Crew**:

- `implement_feature`: Assigned to Dev Agent. Input = spec from Product Manager. Output = implementation description, test results, deployment readiness, completion report.
- `payment_change`: Assigned to Payment Agent. Input = change request. Output = change proposal with compliance considerations, completion report. **Note**: This task triggers a cross-crew audit from the Compliance Agent before Product Manager approval.
- `bug_fix`: Assigned to Dev Agent. Input = bug report with reproduction steps. Output = fix description, root cause, test verification, completion report.

---

### Crew 3: Operations Crew

**Process**: `hierarchical`
**Manager Agent**: Operations Manager
**Worker Agents**: Partner Liaison, Compliance Agent

**Typical task flows**:

- *Partner onboarding*: Partner Liaison vets partner → Ops Manager approves (standard terms) or escalates (custom terms) → Partner Liaison completes onboarding
- *Compliance audit*: Compliance Agent runs scheduled audit → reports findings to Ops Manager → Ops Manager resolves or escalates
- *Cross-audit of payments*: Compliance Agent reviews Payment Agent's work (triggered by Product Crew) → reports to Ops Manager → Ops Manager coordinates with Product Manager

**Tasks defined for this Crew**:

- `vet_partner`: Assigned to Partner Liaison. Input = partner info. Output = vetting report (licenses, ratings, API capability, insurance), completion report.
- `onboard_partner`: Assigned to Partner Liaison. Input = approved partner + terms. Output = onboarding checklist completion, API credentials obtained, commission rate set, completion report.
- `compliance_audit`: Assigned to Compliance Agent. Input = audit scope (jurisdiction, domain). Output = findings with severity, regulation references, recommended actions, completion report.
- `cross_audit_payment`: Assigned to Compliance Agent. Input = payment change proposal from Product Crew. Output = compliance verdict with specific regulation references, completion report.

---

### Crew 4: CX Crew

**Process**: `hierarchical`
**Manager Agent**: CX Manager
**Worker Agents**: Support Agent

**Typical task flows**:

- *Tourist support*: Support Agent handles inquiry → resolves or escalates to CX Manager
- *Refund request*: Support Agent documents request → CX Manager approves (≤$150) or escalates to founder (>$150)
- *Feedback loop*: Support Agent identifies recurring issue → CX Manager creates ticket for Content Crew or Product Crew

**Tasks defined for this Crew**:

- `handle_inquiry`: Assigned to Support Agent. Input = tourist message + booking context. Output = response + resolution status, completion report.
- `process_refund`: Assigned to Support Agent. Input = refund request details. Output = documented justification + tourist interaction history, submitted to CX Manager for approval, completion report.
- `create_feedback_ticket`: Assigned to Support Agent. Input = pattern of recurring issues. Output = structured feedback ticket with data, suggested improvements, target team, completion report.

**Note**: The CX Crew is the most real-time facing. In practice, the Support Agent may run as a persistent agent (always-on) rather than in discrete Crew runs. CrewAI Crews are better suited for batch/task workflows. For real-time chat, consider running the Support Agent outside CrewAI as a standalone persistent agent session, reporting into the CX Crew for review workflows.

---

## 6. Orchestrator Layer (You Build This)

The orchestrator is the glue that sits above CrewAI. It is NOT a CrewAI concept — it's your application code.

### Responsibilities

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

**1. Receives work requests** and routes them to the right Crew. A new content brief goes to Content Crew. A partner application goes to Ops Crew. A bug report goes to Product Crew.

**2. Manages inter-Crew communication.** When the Content Crew publishes a guide, it notifies the CX Crew so Support Agent knows about new content. When Product Crew changes a payment flow, it triggers a cross-audit task in the Ops Crew. These are not internal to any one Crew — the orchestrator handles the handoff.

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

The orchestrator is a Python application (or TypeScript, your choice) that:
- Instantiates the 4 Crews with their agents and task templates
- Exposes an API (or CLI) for submitting work requests
- Maintains state in a database (SQLite for prototype, PostgreSQL for production)
- Runs agent sessions via the executor abstraction (not all running simultaneously)
- Listens for escalation signals and inter-crew communication
- Persists audit logs, scorecards, and agent memory

---

## 7. Founder Interaction Model (Feishu)

### Overview
The founder is the final authority for escalations, approvals, and novel decisions. The founder interacts with the organization via Feishu, using a hybrid bot architecture: each manager agent has its own **custom webhook bot** (with its own name and avatar) for sending messages, while a single **app bot** handles all inbound messages from the founder. This gives you the feel of chatting with 4 distinct managers, without needing 4 separate full Feishu apps.

### Hybrid Bot Architecture

```
OUTBOUND (agents → founder):
  4 custom webhook bots (one per manager, each with own name + avatar)
  └── Content Manager Bot  ──→  posts to group chats / sends cards
  └── Product Manager Bot  ──→  posts to group chats / sends cards
  └── Ops Manager Bot      ──→  posts to group chats / sends cards
  └── CX Manager Bot       ──→  posts to group chats / sends cards

INBOUND (founder → agents):
  1 full app bot ("OPC Hub")
  └── Receives all founder messages via Event Subscription
  └── Parses which manager is being addressed
  └── Routes to orchestrator → spawns agent session → replies via that manager's webhook
```

**Why this hybrid?** Feishu custom webhook bots can send messages with their own identity (name + avatar) but cannot receive or listen for replies. The full app bot ("OPC Hub") fills that gap — it receives all founder messages and the orchestrator routes them to the right manager agent session. The response comes back through that manager's webhook bot, so visually the founder sees a reply from "Content Manager" or "CX Manager," not from a generic bot.

```
Founder types: "@Content Manager what's the status of the Shenzhen guide?"
    │
    ▼
OPC Hub app bot receives the message (Event Subscription)
    │
    ▼
Orchestrator parses target: "Content Manager"
    │
    ▼
Orchestrator spins up Content Manager agent session with context
    │
    ▼
Content Manager agent produces response
    │
    ▼
Orchestrator sends response via Content Manager webhook bot
    │
    ▼
Founder sees reply from "📝 Content Manager" in the group chat
    │
    ▼
Founder replies again → OPC Hub receives → cycle continues
```

### Feishu Group Chat Structure

Each manager gets a dedicated group chat where the founder can have ongoing conversations with that manager. The custom webhook bot for that manager is the primary poster. The OPC Hub app bot is also in every group to receive founder messages.

```
Feishu workspace
│
├── 📝 Content Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Content Manager webhook bot
│   ├── Daily reports from Content Manager
│   ├── Founder ↔ Content Manager conversations
│   └── Content-related escalations and approvals
│
├── ⚙️ Product Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Product Manager webhook bot
│   ├── Daily reports from Product Manager
│   ├── Founder ↔ Product Manager conversations
│   └── Product/payment-related escalations
│
├── 🤝 Ops Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + Ops Manager webhook bot
│   ├── Daily reports from Ops Manager
│   ├── Founder ↔ Ops Manager conversations
│   └── Partner/compliance-related escalations
│
├── 💬 CX Manager (group chat)
│   ├── Members: Founder + OPC Hub bot + CX Manager webhook bot
│   ├── Daily reports from CX Manager
│   ├── Founder ↔ CX Manager conversations
│   └── Support/refund-related escalations
│
├── 🔴 OPC Urgent (group chat)
│   ├── Members: Founder + OPC Hub bot + all 4 webhook bots
│   └── Tier 1 only — safety, security, downtime
│
├── 📊 OPC Weekly (group chat)
│   ├── Members: Founder + OPC Hub bot
│   └── Tier 4 — weekly org-wide summary
│
└── 🔧 OPC System (group chat)
    ├── Members: Founder + OPC Hub bot
    └── Bot health, errors, system notifications
```

This structure means when the founder opens their Feishu, they see group chats named after their managers — just like having 4 direct reports in a real company. Each conversation stays in its domain.

### Four Notification Tiers

#### Tier 1: Immediate (urgent push notification)
**Triggers**: Safety incidents, security breaches, system downtime >30min
**Feishu behavior**: Posted to **OPC Urgent** group by the relevant manager's webhook bot. Interactive card with red severity indicator. @mentions the founder. If no response in 15 minutes, re-sends with escalated urgency.
**Expected response time**: < 30 minutes
**Timeout behavior**: Re-notify every 15 minutes. After 1 hour with no response, the orchestrator takes the most conservative safe action available (e.g., pause the affected service, freeze the affected content) and logs what it did.

**Example (posted by CX Manager webhook bot in OPC Urgent):**
```
🔴 URGENT ESCALATION — Safety Incident

Source: Support Agent → CX Manager → Founder
Category: Tourist Safety
Severity: Critical

Summary: Tourist reports being directed to unlicensed tour
operator by our partner "Macau Adventure Tours". Tourist
is currently at the location and feels unsafe.

Agent recommendation: Immediately suspend Macau Adventure
Tours from the platform. Provide tourist with emergency
contacts and legitimate alternative.

Reply:
  • "approved" — suspend partner, assist tourist
  • "deny" — explain alternative action
  • or ask a question for more context
```

#### Tier 2: Standard (normal notification)
**Triggers**: Budget approvals >$200, refunds >$150, partner disputes, regulatory ambiguity, manager deadlocks, content with potential political sensitivity
**Feishu behavior**: Posted to the **relevant manager's group chat** by that manager's webhook bot. Standard interactive card, no urgent flag.
**Expected response time**: < 24 hours
**Timeout behavior**: Reminder at 12 hours. Second reminder at 20 hours. At 24 hours, re-notify with "escalation aging" flag. Task remains blocked but other work continues.

**Example (posted by CX Manager webhook bot in CX Manager group chat):**
```
🟡 APPROVAL NEEDED — Refund Request

Source: Support Agent → CX Manager → Founder
Category: Refund (above $150 threshold)
Amount: $280 USD

Summary: Tourist booked Hong Kong harbor tour ($280) but
ferry service was cancelled due to typhoon signal. Partner
"HK Harbor Cruises" confirms cancellation. Tourist
requesting full refund. CX Manager recommends full refund
— force majeure, tourist has no fault.

Tourist satisfaction score: 4.5/5 (loyal customer)
Partner SLA status: In compliance (weather exception)

Reply:
  • "approved" — full refund of $280
  • "partial [amount]" — partial refund
  • "deny" — explain reason
  • or ask a question
```

#### Tier 3: Daily Manager Reports
**Triggers**: Each manager agent sends a daily report to the founder.
**Feishu behavior**: Posted to that **manager's group chat** by the manager's webhook bot. The founder can reply in-thread to ask follow-up questions, give new instructions, or adjust priorities — the OPC Hub app bot receives the reply, the orchestrator spins up the manager agent session, and the response comes back via the webhook bot. The conversation continues in-thread until resolved.
**Delivery schedule**: Daily at 9:00 AM (configurable per manager, staggered to avoid a wall of messages).

**Example — posted by Content Manager webhook bot in Content Manager group chat:**
```
📝 Daily Report — Apr 11, 2026

Completed yesterday:
  • Macau visa guide updated (QA passed, published)
  • 2 blog posts in review: "Alipay setup for tourists",
    "HK Airport Express tips"

In progress:
  • Shenzhen day trip guide — Content Writer drafting,
    ETA tomorrow
  • SEO audit of transport guides — 60% complete

Blocked:
  • None

Team health:
  Content Writer: Green (92%) — on track
  QA Agent: Green (96%)
  SEO Agent: Green (91%)

Upcoming this week:
  • Zhuhai border crossing guide (starts Wed)
  • Monthly content freshness review (Fri)

Questions or new priorities? Reply here and I'll adjust.
```

**Example — posted by CX Manager webhook bot in CX Manager group chat:**
```
💬 Daily Report — Apr 11, 2026

Support volume (last 24h): 47 inquiries
  Resolved: 41 (87%)
  Escalated to me: 4
  Pending: 2

Top tourist questions:
  1. "Can I use Alipay without Chinese bank account?" (12x)
  2. "How to get from HK airport to Macau?" (8x)
  3. "Is Macau visa-free for US citizens?" (6x)

Issues flagged:
  • Question #1 is spiking — current guide may be outdated
    since Alipay updated foreign card support last week.
    I've created a content ticket for Content Manager.

Refunds processed: 1 ($85, within my authority)
Refunds pending your approval: 0

Support Agent: Yellow (82%) — resolution rate improving
  (was 78% last week)

Reply here if you want to dig into anything.
```

The founder can reply in any manager's daily report thread:
- "Tell me more about the Alipay question spike" → OPC Hub receives → orchestrator spins up CX Manager → CX Manager webhook bot replies in-thread
- "Reprioritize — I want the Zhuhai guide before Shenzhen" → OPC Hub receives → orchestrator spins up Content Manager → Content Manager webhook bot replies in-thread
- "Why is Support Agent still Yellow?" → CX Manager replies with detailed breakdown in-thread

These threads become ongoing conversations. The orchestrator preserves thread context so follow-up questions work naturally.

#### Tier 4: Weekly Summary
**Triggers**: Weekly org-wide rollup — aggregated view across all managers
**Feishu behavior**: Posted to the **OPC Weekly** group chat by the OPC Hub bot. No push. The founder reads when convenient.
**Delivery schedule**: Weekly every Monday at 10:00 AM

**Example:**
```
📊 Weekly OPC Summary — Week of April 6, 2026

Team Health:
  Content accuracy: 94% ✓
  Payment success: 99.2% ✓
  Support resolution: 82% ⚠ (below 85% target, improving)
  Partner API uptime: 96% ⚠

Agent Scorecards:
  Content Writer: Green (92%)
  QA Agent: Green (96%)
  Support Agent: Yellow (82%) — up from 78%
  All others: Green

Escalations this week: 2
  • Refund $280 (approved by you on Apr 8)
  • Partner SLA dispute (resolved by Ops Manager)

Founder interactions this week: 5
  • 2 escalation approvals
  • 1 priority adjustment (via Content Manager chat)
  • 2 status inquiries

Open items: 0
```

### Reply Parsing

The OPC Hub app bot receives all founder messages and parses them into structured decisions:

| Founder reply | Parsed as | Orchestrator action |
|---|---|---|
| "approved" / "yes" / "go ahead" / "ok" | `decision: approved` | Resume task with approval |
| "denied" / "no" / "reject" | `decision: denied` | Resume task with denial, agent adjusts |
| "approved, but [condition]" | `decision: approved_conditional` | Resume task with condition injected into context |
| "partial 150" (for refunds) | `decision: partial, amount: 150` | Resume task with partial approval |
| "why?" / "tell me more" / any question | `decision: needs_info` | Orchestrator gathers more context from the relevant agent and sends a follow-up message |
| "hold" / "let me think" / "later" | `decision: deferred` | Reset timeout clock, task stays waiting |
| No reply within timeout | `decision: timeout` | Follow timeout behavior for that tier |

If the bot can't confidently parse the reply, it asks for clarification: "I didn't understand that. Could you reply with 'approved', 'denied', or ask a specific question?"

### Founder-Initiated Interaction

The founder doesn't only respond to escalations — they can reach into the system at any time to check on things, set goals, or have a conversation with any manager.

#### Talking to a manager

The founder simply opens the relevant manager's group chat and posts a message:

```
Founder (in Content Manager group chat):
  What's the status of the Shenzhen day trip guide?
```

The OPC Hub receives this, the orchestrator identifies it's in the Content Manager group, spins up a Content Manager session with context, and the Content Manager webhook bot replies:

```
Content Manager:
  The Shenzhen day trip guide is currently with Content Writer,
  about 60% drafted. ETA for first draft is tomorrow morning.
  QA review scheduled for Tuesday. On track for Wednesday publication.
```

The founder can continue the conversation naturally:

```
Founder: Push that to top priority. I want it published by Wednesday.

Content Manager: Understood. I'll move it ahead of the Zhuhai guide
and brief the Content Writer now. The QA review will need to happen
Tuesday to hit Wednesday publication. I'll flag you if there's a
blocker.

Founder: Also — make sure it covers the new Shenzhen metro Line 20.
I saw it opened last week.

Content Manager: Good catch. I'll add that to the brief and make sure
QA verifies the station names and operating hours. Noted in the
knowledge base for future Shenzhen content.
```

Each reply from the founder spawns a new agent session for that manager with the full thread history as context. The manager agent can take real actions in response — updating task priorities, briefing worker agents, adding to the knowledge base — not just generating text.

#### Setting goals and directives

The founder posts in the relevant manager's group chat:

```
Founder (in Ops Manager group chat):
  I want to onboard 3 new hotel partners in Macau this month.
  Focus on 4-star properties near the Cotai Strip. Budget up to
  $500 total for onboarding costs.

Ops Manager: I'll get Partner Liaison started on sourcing candidates.
Here's my plan:
  Week 1: Identify and vet 5-6 candidates (expect some won't qualify)
  Week 2: Begin onboarding top 3
  Week 3-4: Complete API integration and go live

I'll include progress in my daily reports. The $500 budget is within
your auto-approved threshold so I can proceed. Should I flag you
before committing to each partner, or only if something unusual
comes up?

Founder: Only flag me if something unusual comes up. You have
authority on standard terms.

Ops Manager: Confirmed. I'll proceed with standard terms (10-20%
commission, standard SLA, 3-month initial contract) and only
escalate for exceptions.
```

The orchestrator logs this directive as a standing instruction — it gets added to the Ops Manager's context for future sessions and tracked as a goal in the performance system.

#### Quick commands (in any group chat or DM with OPC Hub)

For simple checks, the founder can use short commands:

| Command | Response |
|---|---|
| `status` | Current system status, pending approvals, active tasks |
| `scorecard content_writer` | Content Writer's current 30-day scorecard |
| `scorecard all` | Summary scorecards for all agents |
| `pending` | List all tasks waiting for founder input |
| `pause support_agent` | Pause the Support Agent (with confirmation) |
| `resume support_agent` | Resume a paused agent |
| `health` | Team health summary |

These are handled directly by the orchestrator without spinning up a full agent session — they're fast lookups from the database.

#### When the founder's message becomes a task

Sometimes a conversation with a manager leads to new work:

```
Founder (in Product Manager group chat):
  Tourists are complaining that the booking page is slow on mobile
  in mainland China. Can you investigate?

Product Manager: I'll have Dev Agent run a performance audit focusing
on mobile + China network conditions. Likely causes are CDN routing,
unoptimized images, or third-party scripts being blocked by the
Great Firewall. I'll report back within 24 hours.
```

The Product Manager creates a new task (performance audit), assigns it to the Dev Agent, and tracks it. The orchestrator logs the founder's original message as the task origin. The Product Manager's next daily report will include this task's progress.

### Feishu Bot Setup Requirements

**OPC Hub (full app bot) — 1 app:**
- Created on Feishu Open Platform (open.feishu.cn)
- App ID and App Secret stored in orchestrator config (encrypted)
- Bot capability enabled
- Event Subscription: `im.message.receive_v1` (to receive founder messages)
- Permissions: `im:message`, `im:message.group_at_msg`, `im:chat`
- Added to all group chats as a member

**Manager webhook bots — 4 custom bots:**
- Created in each manager's group chat via "Add Custom Bot"
- Each has its own name and avatar:
  - 📝 Content Manager (avatar: pen/notebook icon)
  - ⚙️ Product Manager (avatar: gear icon)
  - 🤝 Ops Manager (avatar: handshake icon)
  - 💬 CX Manager (avatar: speech bubble icon)
- Each provides a webhook URL stored in orchestrator config
- Outbound only: `POST {webhook_url}` with message payload
- Optional: webhook signing key for security

**Setup summary:**
- 1 Feishu Open Platform app (OPC Hub) — handles all inbound
- 4 custom webhook bots — handle outbound with distinct identities
- 7 group chats (4 manager chats + urgent + weekly + system)

### Audit Trail

Every founder interaction is logged:
```json
{
  "escalation_id": "ESC-2026-0042",
  "feishu_message_id": "om_abc123def456",
  "group_chat": "cx_manager",
  "sent_at": "2026-04-11T14:30:00Z",
  "sent_via": "cx_manager_webhook",
  "tier": "standard",
  "founder_reply": "approved, but verify partner insurance is current first",
  "received_via": "opc_hub_app",
  "replied_at": "2026-04-11T15:12:00Z",
  "response_time_minutes": 42,
  "parsed_decision": "approved_conditional",
  "condition": "verify partner insurance is current first",
  "task_resumed_at": "2026-04-11T15:12:05Z"
}
```

This data feeds into the founder dashboard and helps calibrate whether escalation thresholds are set correctly — if the founder is always approving a certain category, the threshold might be too low.

---

## 8. Tools Each Agent Gets

Agents running as full coding-agent sessions have native access to file system, shell, and web. The tools below are *additional* structured tools surfaced through CrewAI or MCP:

### Shared tools (all agents)
- `read_knowledge_base(topic)` — query the org charter, SOPs, brand guidelines
- `submit_completion_report(report)` — mandatory after every task
- `escalate(category, severity, summary)` — trigger the escalation router
- `view_team_health()` — see the current team health summary
- `record_learning(insight)` — append to agent's learnings file

### Content Writer
- `search_web(query)` — research destinations, verify facts
- `check_source(url)` — verify an official source is current

### QA Agent
- `search_web(query)` — verify claims against official sources
- `check_url(url)` — test if a link is live
- `check_exchange_rate(from, to)` — verify currency conversions

### SEO Agent
- `keyword_research(seed_keywords)` — find tourist intent queries
- `analyze_serp(keyword)` — check current rankings and competitors

### Dev Agent
- `run_tests(scope)` — execute test suite
- `check_performance(url)` — measure page load times
- `deploy(target)` — deploy to staging/production (with approval)

### Payment Agent
- `check_gateway_status(gateway)` — verify Stripe/Alipay/WeChat status
- `get_exchange_rate(from, to)` — current market rates

### Partner Liaison
- `search_partner(criteria)` — find potential partners
- `check_business_license(entity)` — verify partner credentials
- `update_partner_directory(entry)` — update partner directory in KB

### Compliance Agent
- `search_regulation(jurisdiction, topic)` — find current regulations
- `log_audit_finding(finding)` — log to compliance audit trail

### Support Agent
- `lookup_booking(booking_id)` — retrieve booking details
- `submit_feedback_ticket(pattern, data)` — create feedback for CX Manager
- `get_emergency_info(jurisdiction)` — retrieve local emergency numbers

---

## 9. Performance Tier Impact on Crew Configuration

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

## 10. Permission and Authority Model

### Problem
Each agent runs as a coding-agent session with real capabilities — file access, shell commands, network requests, API calls. Without constraints, a Content Writer could modify payment code, or a Support Agent could deploy to production. The permission layer enforces the authority boundaries defined in the org charter and each agent's system prompt.

### How permissions are configured

The orchestrator generates a permission policy for each agent session based on three inputs:

```
Permission policy = Agent's role scope (from system prompt)
                  + Org charter limits (budget, authority levels)
                  + Current performance tier (green/yellow/red reduces scope)
```

Each executor handles permissions differently:

| Executor | Permission mechanism |
|---|---|
| `claude-code` | `--allowedTools` flags + settings file scoping allowed operations |
| `codex` | Sandbox configuration + approved command list |
| `opencode` | Config-based tool restrictions |
| `crewai-native` | Tools list on the Agent object (only listed tools are available) |

### Permission scopes per agent

```
agent_permissions:
  content_writer:
    file_read:
      - knowledge_base/**
      - agent_memory/content_writer/**
      - content_drafts/**
    file_write:
      - content_drafts/**
      - agent_memory/content_writer/learnings.md
    shell: false
    network:
      - "*.gov.mo"           # Macau government sites
      - "*.gov.hk"           # HK government sites
      - "*.gov.cn"           # Mainland government sites
      - "en.wikipedia.org"
      - search engines
    apis: []

  qa_agent:
    file_read:
      - knowledge_base/**
      - agent_memory/qa_agent/**
      - content_drafts/**
    file_write:
      - qa_reviews/**
      - agent_memory/qa_agent/learnings.md
      - agent_memory/qa_agent/known_issues.md
    shell:
      - "curl -I *"          # HEAD requests for link checking
    network: ["*"]           # Needs broad access for fact-checking
    apis:
      - exchange_rate_api

  dev_agent:
    file_read:
      - src/**
      - tests/**
      - knowledge_base/technical/**
      - agent_memory/dev_agent/**
    file_write:
      - src/**
      - tests/**
      - agent_memory/dev_agent/learnings.md
    shell:
      - "npm *"
      - "python *"
      - "pytest *"
      - "git *"              # But not git push to production
    network:
      - "registry.npmjs.org"
      - "pypi.org"
      - partner API domains (from partner directory)
    apis:
      - partner_apis (read-only in dev, read-write in staging)
    blocked:
      - "rm -rf *"
      - "git push origin main"
      - any write to src/payments/** (requires Payment Agent)

  payment_agent:
    file_read:
      - src/payments/**
      - knowledge_base/compliance/**
      - agent_memory/payment_agent/**
    file_write:
      - src/payments/**
      - agent_memory/payment_agent/learnings.md
    shell:
      - "python *"
      - "pytest src/payments/**"
    network:
      - "api.stripe.com"
      - "*.alipay.com"
      - "*.wechatpay.com"
    apis:
      - stripe_api
      - alipay_api
      - wechatpay_api
    blocked:
      - any deployment action
      - any write outside src/payments/**

  # ... similar scopes for remaining agents
```

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
**Example**: Payment Agent proposes a payment flow change, but it needs Compliance Agent review before Product Manager can approve. Dev Agent needs to implement a feature that requires a partner API endpoint, but Partner Liaison hasn't onboarded that partner yet.
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

---

## 11. What CrewAI Handles vs. What You Build

### CrewAI handles
- Task definitions (description, expected output, assigned agent, context chaining)
- Sequential and hierarchical execution within a single Crew
- Manager delegation and review (hierarchical process)
- Tool execution by agents (when using crewai-native executor)

### You build (orchestrator layer)
- Agent executor abstraction (spawn coding-agent sessions per configured backend)
- Agent memory management (context assembly, write-back, learnings consolidation)
- Permission policy generation and enforcement
- Task state machine (including blocked/waiting states and timeouts)
- Founder interaction via Feishu (hybrid bot architecture, notifications, reply parsing, decision routing)
- Inter-Crew communication and task routing
- The escalation router (12 rules from your escalation doc)
- The revision loop (re-triggering Crew runs with feedback)
- Performance scoring and tier management
- Dynamic Crew configuration based on tiers
- Audit logging (wrap execution callbacks)
- The founder dashboard
- Knowledge base with scoped access (RAG layer)
- Real-time support (Support Agent as persistent agent, not batch Crew)

### Consider alternatives for
- **Real-time support**: CrewAI is batch-oriented. The Support Agent might run better as a standalone persistent agent session, reporting into the CX Crew for review workflows
- **Complex state machines**: If revision loops, conditional branching, and inter-crew dependencies get complex, LangGraph gives you explicit graph-based control. You could use LangGraph for the orchestrator and CrewAI for individual Crew execution — they're not mutually exclusive

---

## 12. Founder Dashboard

### Purpose
The Feishu chats are for real-time interaction — conversations, approvals, daily updates. The dashboard is for when you want the big picture: how is the org performing, what happened recently, where are the trends going, and what's the full audit trail. It runs as a local web app on your Mac Mini, accessible from any device on your network.

### Self-Hosted, No CrewAI AMP
This dashboard replaces CrewAI's cloud-based AMP platform entirely. CrewAI AMP is **not used** — all data stays on your Mac Mini. The open-source CrewAI framework provides task callbacks that the orchestrator hooks into to capture execution data (agent decisions, task timelines, tool usage, LLM calls). This data is written to your local database alongside the business-level metrics (scorecards, escalation history, calibration insights). One dashboard covers both engineering observability and business performance — no cloud dependency, no data leaving your infrastructure, full compliance with PIPL/PDPO/PDPA.

### Tech Stack
- **Backend**: FastAPI (Python) — reads from the same SQLite database the orchestrator writes to
- **Frontend**: Lightweight React app (or plain HTML + Chart.js for simplicity)
- **Hosting**: Runs on your Mac Mini alongside the orchestrator, served on a local port (e.g., `http://mac-mini.local:8080`)
- **Auth**: Simple token or password gate (this is a local network app, not public-facing)
- **Refresh**: Auto-refreshes every 60 seconds, or manual refresh
- **Observability**: CrewAI task callbacks → orchestrator → SQLite (no external tracing service needed). Optionally add OpenTelemetry with a self-hosted Jaeger/Grafana if you want richer execution traces later

### Dashboard Layout

#### Page 1: Live Status

The landing page — what's happening right now.

```
┌──────────────────────────────────────────────────────────┐
│  OPC Dashboard — Live Status                    [Refresh] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  SYSTEM HEALTH          PENDING YOUR ACTION              │
│  ┌────────────────┐     ┌──────────────────────────────┐ │
│  │ ● All systems  │     │ 🟡 Refund $280 (CX Mgr)     │ │
│  │   operational  │     │    waiting 3h — reply in      │ │
│  │                │     │    Feishu to approve/deny     │ │
│  │ Uptime: 99.7%  │     │                              │ │
│  │ Last incident: │     │ 🟡 Partner custom terms       │ │
│  │   3 days ago   │     │    (Ops Mgr) waiting 1h      │ │
│  └────────────────┘     └──────────────────────────────┘ │
│                                                          │
│  ACTIVE TASKS BY CREW                                    │
│  ┌──────────────────────────────────────────────────────┐│
│  │ Content Crew                                         ││
│  │   ● Content Writer: Drafting "Shenzhen day trip"     ││
│  │   ○ QA Agent: Idle (waiting for draft)               ││
│  │   ○ SEO Agent: Idle                                  ││
│  │                                                      ││
│  │ Product Crew                                         ││
│  │   ● Dev Agent: Implementing mobile perf fixes        ││
│  │   ○ Payment Agent: Idle                              ││
│  │                                                      ││
│  │ Ops Crew                                             ││
│  │   ● Partner Liaison: Vetting Macau hotel candidates  ││
│  │   ○ Compliance Agent: Idle                           ││
│  │                                                      ││
│  │ CX Crew                                              ││
│  │   ● Support Agent: Handling inquiries (3 active)     ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  BLOCKED TASKS                                           │
│  ┌──────────────────────────────────────────────────────┐│
│  │ (none currently)                                     ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

Data sources: orchestrator task state database (real-time), escalation queue

#### Page 2: Agent Scorecards

Rolling 30-day performance for every agent.

```
┌──────────────────────────────────────────────────────────┐
│  Agent Scorecards — 30 Day Rolling          [Export CSV] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Agent            Tier    Acceptance  Revision  Errors   │
│  ─────────────────────────────────────────────────────   │
│  Content Writer   🟢 92%    92%        6%       2       │
│  QA Agent         🟢 96%    96%        3%       0       │
│  SEO Agent        🟢 91%    91%        7%       1       │
│  Dev Agent        🟢 94%    94%        4%       1       │
│  Payment Agent    🟢 98%    98%        2%       0       │
│  Partner Liaison  🟢 90%    90%        8%       2       │
│  Compliance Agent 🟢 95%    95%        5%       0       │
│  Support Agent    🟡 82%    82%       12%       4       │
│                                                          │
│  [Click any agent for detailed breakdown]                │
│                                                          │
│  ─── CALIBRATION ────────────────────────────────────    │
│                                                          │
│  Agent            Avg Confidence  Actual Accuracy  Gap   │
│  ─────────────────────────────────────────────────────   │
│  Content Writer       85%             88%          -3%   │
│  QA Agent             90%             92%          -2%   │
│  Support Agent        78%             72%          +6% ⚠ │
│                                                          │
│  ⚠ Support Agent is overconfident — flags issues as      │
│    resolved but 6% are reopened by tourists              │
└──────────────────────────────────────────────────────────┘
```

Clicking an agent opens a detail view: task-by-task history, confidence vs. outcome for each task, trend line over the 30-day window, and the agent's current learnings file.

Data sources: performance tracker database, agent memory files

#### Page 3: Work Log (Audit Trail)

Searchable, filterable log of every action in the system.

```
┌──────────────────────────────────────────────────────────┐
│  Work Log                                                │
│  Filter: [Agent ▼] [Date range] [Type ▼] [Search...]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Apr 11, 14:32  Content Writer  wrote_content            │
│    Task: Shenzhen day trip guide (first draft)           │
│    Confidence: 82                                        │
│    Status: Submitted for QA review                       │
│                                                          │
│  Apr 11, 14:15  QA Agent  reviewed_content_pass          │
│    Task: Macau visa guide (v2)                           │
│    Verdict: PASS                                         │
│    Checklist: 10/10 items verified                       │
│                                                          │
│  Apr 11, 13:50  Content Manager  decided_approve         │
│    Task: Macau visa guide (v2)                           │
│    Decision: Approved for publication                    │
│                                                          │
│  Apr 11, 12:00  CX Manager  escalated                   │
│    Task: Refund request $280                             │
│    Reason: Above $150 threshold                          │
│    Status: Waiting for founder approval                  │
│                                                          │
│  Apr 11, 09:00  Content Manager  daily_report            │
│    Channel: Feishu Content Manager group                 │
│                                                          │
│  [Load more...]                                          │
│                                                          │
│  Showing 50 of 312 entries this week                     │
└──────────────────────────────────────────────────────────┘
```

Each entry is expandable to show the full completion report, including risks flagged, dependencies, and suggested reviewer focus. Every entry links back to the original task.

Data sources: audit logger (JSONL files or SQLite audit table)

#### Page 4: Escalation History

Every escalation that was routed, how it was resolved, and whether the system is calibrated correctly.

```
┌──────────────────────────────────────────────────────────┐
│  Escalation History                    [This month ▼]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  SUMMARY                                                 │
│  Total escalations: 8                                    │
│  To founder: 5  |  Resolved by manager: 3               │
│  Avg response time: 2.4 hours                            │
│  Warranted: 7/8 (88%)  |  Could have been avoided: 1    │
│                                                          │
│  ESCALATION LOG                                          │
│  ─────────────────────────────────────────────────────   │
│  ESC-042  Apr 11  Refund $280         CX Mgr → Founder  │
│           Tier 2  ⏳ Pending (3h)                        │
│                                                          │
│  ESC-041  Apr 10  Partner custom      Ops Mgr → Founder │
│           Tier 2  ✅ Approved (1.5h response)            │
│                                                          │
│  ESC-040  Apr 8   Content political   Content Mgr → Fdr │
│           Tier 2  ✅ Approved w/ edits (4h response)     │
│                                                          │
│  ESC-039  Apr 7   Payment failure     Product Mgr → Fdr │
│           Tier 1  ✅ Resolved (22min response)           │
│                                                          │
│  CALIBRATION INSIGHTS                                    │
│  ┌──────────────────────────────────────────────────────┐│
│  │ "Refund" escalations: 3 this month, all approved.    ││
│  │ Consider raising CX Manager's refund authority       ││
│  │ from $150 to $300 to reduce founder involvement.     ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

The "calibration insights" section is generated by the orchestrator — if the founder always approves a certain category, it suggests raising the threshold. If a manager keeps escalating things that didn't need escalating, it flags that too.

Data sources: escalation router database, founder response logs

#### Page 5: Trends

Charts showing how the org is performing over time.

```
┌──────────────────────────────────────────────────────────┐
│  Trends                         [7d] [30d] [90d] [All]  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  CONTENT OUTPUT          SUPPORT QUALITY                 │
│  ┌─────────────────┐     ┌─────────────────┐            │
│  │ Guides: ████▌   │     │ Resolution ████ │            │
│  │         12/mo   │     │ rate: 82%  ▲4%  │            │
│  │ Blog:  ████████ │     │                 │            │
│  │         32/mo   │     │ Satisfaction    │            │
│  │                 │     │ 4.3/5 ▲0.1     │            │
│  └─────────────────┘     └─────────────────┘            │
│                                                          │
│  PAYMENT HEALTH          PARTNER SLA                     │
│  ┌─────────────────┐     ┌─────────────────┐            │
│  │ Success: 99.2%  │     │ Compliance:     │            │
│  │ ██████████████▌ │     │ 96% ▼1%        │            │
│  │                 │     │                 │            │
│  │ Chargebacks:    │     │ Active: 12      │            │
│  │ 0.3% ✓         │     │ Onboarding: 3   │            │
│  └─────────────────┘     └─────────────────┘            │
│                                                          │
│  AGENT PERFORMANCE OVER TIME                             │
│  ┌──────────────────────────────────────────────────────┐│
│  │  100%|     ·····●·····●·····●   Content Writer      ││
│  │   90%|  ●·····●·····●          QA Agent             ││
│  │   80%|                ●····●·  Support Agent ⚠      ││
│  │   70%|  ·                                           ││
│  │      +----+----+----+----+----                      ││
│  │      W1   W2   W3   W4   W5                        ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  FOUNDER INVOLVEMENT                                     │
│  ┌──────────────────────────────────────────────────────┐│
│  │ Escalations/week: 2.3 avg (target: ≤3) ✓            ││
│  │ Avg response time: 2.4h (improving from 3.1h)       ││
│  │ Time spent on approvals: ~25 min/week               ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

The "Founder Involvement" section is key — it shows whether the system is trending toward more autonomy (fewer escalations, faster resolution) or more dependency on you. The goal is for this number to decrease over time as thresholds are calibrated and agents improve.

Data sources: all databases (task history, performance tracker, escalation logs, audit trail)

#### Page 6: Execution Traces (replaces CrewAI AMP)

Engineering-level observability for debugging and cost tracking. This is what CrewAI AMP would have provided, but self-hosted.

```
┌──────────────────────────────────────────────────────────┐
│  Execution Traces                      [Today ▼]        │
│  Filter: [Crew ▼] [Agent ▼] [Status ▼] [Search...]     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  COST SUMMARY (today)                                    │
│  Total LLM calls: 47  |  Total tokens: 182,400          │
│  Input: 134,200  |  Output: 48,200                      │
│  Estimated cost: $4.82                                   │
│                                                          │
│  RECENT CREW RUNS                                        │
│  ─────────────────────────────────────────────────────   │
│  ▶ RUN-087  Content Crew  14:32  ✅ 3m 22s  $0.84      │
│    ├── Content Writer  write_content     2m 10s  $0.52  │
│    │   └── LLM: 3 calls, 12,400 tokens                 │
│    │   └── Tools: search_web (2x), check_source (1x)   │
│    ├── QA Agent  qa_review               0m 48s  $0.24  │
│    │   └── LLM: 2 calls, 5,200 tokens                  │
│    │   └── Tools: check_url (4x), check_exchange_rate   │
│    └── Content Manager  manager_review   0m 24s  $0.08  │
│        └── LLM: 1 call, 2,100 tokens                   │
│                                                          │
│  ▶ RUN-086  CX Crew  13:15  ✅ 1m 05s  $0.31           │
│    ├── Support Agent  handle_inquiry     0m 52s  $0.28  │
│    └── CX Manager  (not invoked — resolved by agent)    │
│                                                          │
│  ▶ RUN-085  Content Crew  12:00  ⚠ 5m 44s  $1.62       │
│    ├── Content Writer  write_content     2m 30s  $0.58  │
│    ├── QA Agent  qa_review  → REVISE     0m 55s  $0.26  │
│    ├── Content Writer  revision_1        1m 45s  $0.52  │
│    └── QA Agent  qa_review  → PASS       0m 34s  $0.18  │
│    Note: 1 revision round (currency info outdated)      │
│                                                          │
│  [Click any run for full trace detail]                   │
│                                                          │
│  COST TREND (last 30 days)                               │
│  ┌──────────────────────────────────────────────────────┐│
│  │  $8 |          ·                                     ││
│  │  $6 |    ·  ·     ·  ·                               ││
│  │  $4 |  ·       ·       ·  ·  ·  ·                   ││
│  │  $2 |                                                ││
│  │     +----+----+----+----+----                        ││
│  │     W1   W2   W3   W4   W5                          ││
│  │  Avg: $5.20/day                                      ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

Clicking a run expands the full trace: every LLM call with input/output, tool invocations with arguments and results, timing breakdown, token counts, and the executor used (Claude Code, Codex, etc.). This is captured via CrewAI's task callbacks — the orchestrator hooks `on_task_start`, `on_task_end`, `on_tool_use`, and logs everything to SQLite.

Data sources: orchestrator execution logs (captured via CrewAI callbacks), LLM usage tracking

### Dashboard API Endpoints

The dashboard backend exposes a REST API that the frontend consumes. This same API could be used by other tools (scripts, Feishu bot commands, etc.):

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Live system status, active tasks, blocked tasks, pending approvals |
| `GET /api/scorecards` | All agent scorecards with 30-day rolling metrics |
| `GET /api/scorecards/{agent}` | Detailed scorecard for one agent |
| `GET /api/logs?agent=&date=&type=` | Filtered audit trail entries |
| `GET /api/logs/{task_id}` | Full history for a specific task |
| `GET /api/escalations?period=` | Escalation history with calibration insights |
| `GET /api/trends?period=7d` | Aggregated metrics for charts |
| `GET /api/health` | Team health summary (same data as Feishu `health` command) |
| `GET /api/traces?crew=&date=` | Execution traces with LLM calls, tool usage, timing |
| `GET /api/traces/{run_id}` | Full trace detail for a specific crew run |
| `GET /api/costs?period=7d` | LLM cost breakdown by agent, crew, and time period |

### Connection to Feishu

The dashboard and Feishu are two views of the same data. Quick commands in Feishu (`status`, `health`, `scorecard`) hit the same API endpoints as the dashboard. The "Pending Your Action" items on the dashboard link to the corresponding Feishu group chat thread where you can approve/deny. The dashboard is read-only — all actions (approvals, directives, goal-setting) happen through Feishu conversations.

---

## 13. Suggested Implementation Order

1. **Content Crew only** — the simplest, most self-contained unit. Content Writer + QA Agent + Content Manager with hierarchical process. Get the basic write → review → approve flow working.

2. **Agent executor abstraction** — build the layer that can spawn a coding-agent session (start with one provider, e.g., Claude Code) with the right context files. This is the foundation for everything else.

3. **Add audit logging** — wrap each agent session with callbacks that log every task start, completion, and review to your audit store.

4. **Add the revision loop** — orchestrator logic to re-run when QA returns REVISE, with revision count tracking and escalation after max rounds.

5. **Add agent memory** — implement the learnings file write-back, scorecard injection, and periodic consolidation.

6. **Add performance scoring** — after each Crew run, score the agents. Start displaying scorecards. Implement tier-based task chain adjustment.

7. **Add Feishu bot integration** — set up the OPC Hub app bot and 4 manager webhook bots. Create the 7 group chats. Implement daily reports (Tier 3), standard approvals (Tier 2), reply parsing, and the decision-routing loop. Test with the Content Manager group chat first.

8. **Add the knowledge base** — RAG access to org charter, guides, and SOPs. Scoped read/write per agent.

9. **Stand up Crew 2 (Product) and Crew 3 (Ops)** — these are more complex due to cross-crew dependencies (Compliance Agent cross-auditing Payment Agent).

10. **Add inter-Crew communication** — the orchestrator routes cross-crew tasks.

11. **Stand up CX Crew** — with the Support Agent as a persistent session for real-time chat or inside CrewAI for review workflows.

12. **Add additional executor support** — once the abstraction is solid with one provider, add Codex and OpenCode as options.

13. **Build the founder dashboard** — FastAPI backend exposing the REST API, React or static HTML frontend with the 6 pages (live status, scorecards, work log, escalation history, trends, execution traces). Connect the Feishu quick commands to the same API endpoints. No CrewAI AMP dependency — all data captured via CrewAI callbacks and stored locally.
