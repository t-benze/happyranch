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

## 2. Crew Definitions

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

**Note**: The CX Crew is the most real-time facing. In practice, the Support Agent may run as a persistent agent (always-on) rather than in discrete Crew runs. CrewAI Crews are better suited for batch/task workflows. For real-time chat, consider running the Support Agent outside CrewAI (as a standalone agent with tool access) and using CrewAI only for the CX Manager's review and feedback loop workflows.

---

## 3. Orchestrator Layer (You Build This)

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

**6. Provides the founder dashboard.** Aggregates audit logs, escalation summaries, agent scorecards, and team health metrics into a weekly report.

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
- Runs CrewAI Crews as needed (not all running simultaneously)
- Listens for escalation signals and inter-crew communication
- Persists audit logs and scorecards

---

## 4. Tools Each Agent Gets

CrewAI agents can be given `tools` — functions they can call during execution. Here's what each agent needs:

### Shared tools (all agents)
- `read_knowledge_base(topic)` — query the org charter, SOPs, brand guidelines
- `submit_completion_report(report)` — mandatory after every task
- `escalate(category, severity, summary)` — trigger the escalation router
- `view_team_health()` — see the current team health summary

### Content Writer
- `search_web(query)` — research destinations, verify facts
- `read_knowledge_base(topic)` — access guides, style guide
- `check_source(url)` — verify an official source is current

### QA Agent
- `search_web(query)` — verify claims against official sources
- `check_url(url)` — test if a link is live
- `check_exchange_rate(from, to)` — verify currency conversions
- `read_knowledge_base(topic)` — access verification standards

### SEO Agent
- `keyword_research(seed_keywords)` — find tourist intent queries
- `analyze_serp(keyword)` — check current rankings and competitors
- `read_knowledge_base(topic)` — access SEO standards

### Dev Agent
- `run_tests(scope)` — execute test suite
- `check_performance(url)` — measure page load times
- `read_knowledge_base(topic)` — access technical architecture docs

### Payment Agent
- `check_gateway_status(gateway)` — verify Stripe/Alipay/WeChat status
- `get_exchange_rate(from, to)` — current market rates
- `read_knowledge_base(topic)` — access payment SOPs and PCI requirements

### Partner Liaison
- `search_partner(criteria)` — find potential partners
- `check_business_license(entity)` — verify partner credentials
- `read_knowledge_base(topic)` — access partner standards and templates
- `write_knowledge_base(partner_directory, entry)` — update partner directory

### Compliance Agent
- `search_regulation(jurisdiction, topic)` — find current regulations
- `read_knowledge_base(topic)` — access regulatory summaries
- `write_knowledge_base(compliance_log, entry)` — log audit findings

### Support Agent
- `lookup_booking(booking_id)` — retrieve booking details
- `search_knowledge_base(query)` — find answers in guides and FAQs
- `submit_feedback_ticket(pattern, data)` — create feedback for CX Manager
- `get_emergency_info(jurisdiction)` — retrieve local emergency numbers

---

## 5. Performance Tier Impact on Crew Configuration

The orchestrator dynamically adjusts how Crews run based on agent performance tiers.

### Green tier (>90% acceptance)
- Standard flow: task → agent executes → next step
- Minimal supervision from manager agent

### Yellow tier (75-90%)
- Manager agent reviews ALL output before it proceeds
- In CrewAI terms: add an explicit `manager_review` Task after the agent's Task, with `human_input=False` (the manager agent reviews, not a human)

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

## 6. What CrewAI Handles vs. What You Build

### CrewAI handles
- Agent definitions (role, goal, backstory, tools)
- Task definitions (description, expected output, assigned agent, context chaining)
- Sequential and hierarchical execution within a single Crew
- Manager delegation and review (hierarchical process)
- Tool execution by agents
- Memory within a Crew run (short-term)

### You build (orchestrator layer)
- Inter-Crew communication and task routing
- The escalation router (12 rules from your escalation doc)
- The revision loop (re-triggering Crew runs with feedback)
- Performance scoring and tier management
- Dynamic Crew configuration based on tiers
- Audit logging (wrap CrewAI callbacks)
- The founder dashboard
- Knowledge base with scoped access (RAG layer)
- Long-term memory across Crew runs
- Real-time support (Support Agent as persistent agent, not batch Crew)

### Consider alternatives for
- **Real-time support**: CrewAI is batch-oriented. The Support Agent might run better as a standalone LangChain agent or direct API call with tool access, reporting into the CX Crew for review workflows
- **Complex state machines**: If revision loops, conditional branching, and inter-crew dependencies get complex, LangGraph gives you explicit graph-based control. You could use LangGraph for the orchestrator and CrewAI for individual Crew execution — they're not mutually exclusive

---

## 7. Suggested Implementation Order

1. **Content Crew only** — the simplest, most self-contained unit. Content Writer + QA Agent + Content Manager with hierarchical process. Get the basic write → review → approve flow working.

2. **Add audit logging** — wrap the Content Crew with callbacks that log every task start, completion, and review to your audit store.

3. **Add the revision loop** — orchestrator logic to re-run the Crew when QA returns REVISE, with revision count tracking and escalation after max rounds.

4. **Add performance scoring** — after each Crew run, score the agents. Start displaying scorecards.

5. **Add the knowledge base** — give agents RAG access to the org charter, guides, and SOPs.

6. **Stand up Crew 2 (Product) and Crew 3 (Ops)** — these are more complex due to cross-crew dependencies (Compliance Agent cross-auditing Payment Agent).

7. **Add inter-Crew communication** — the orchestrator routes cross-crew tasks.

8. **Stand up CX Crew** — with the Support Agent either inside CrewAI for review workflows or standalone for real-time chat.

9. **Build the founder dashboard** — aggregate everything into a weekly view.
