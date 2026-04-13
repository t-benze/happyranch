# Crews, Agents & Tools

How the org design maps to CrewAI and what each agent can do.

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

### Crew 2: Product & Engineering Crew

**Process**: `hierarchical`
**Manager Agent**: Engineering Head
**Worker Agents**: Product Manager, Dev Agent, Payment Agent

**Typical task flows**:

- *Feature development*: Engineering Head receives feature request → assigns Product Manager to write spec → routes spec to Dev Agent for implementation → Engineering Head reviews result
- *Bug fix*: Engineering Head receives bug report → assigns Product Manager to triage (severity, repro, priority) → routes triage to Dev Agent for fix → Engineering Head verifies
- *Payment flow change*: Engineering Head assigns Payment Agent to draft proposal → cross-audit requested (stubbed until Ops Crew is built — auto-approved) → Engineering Head reviews proposal

**Revision loop**: In all flows, the revision targets the worker who produced the output. Engineering Head decides who needs to revise. Max 2 rounds before escalation to founder.

**Tasks defined for this Crew**:

- `implement_feature`: Product Manager writes spec (input = feature request), then Dev Agent implements (input = spec). Output = implementation description, test results, deployment readiness, completion report.
- `bug_fix`: Product Manager triages (input = bug report), then Dev Agent fixes (input = triage report). Output = fix description, root cause, test verification, completion report.
- `payment_change`: Assigned to Payment Agent. Input = change request. Output = change proposal with compliance considerations, completion report. **Note**: This task would normally trigger a cross-crew audit from the Compliance Agent. Until Ops Crew is built, the cross-audit is stubbed (logged and auto-approved).

---

### Crew 3: Operations Crew

**Process**: `hierarchical`
**Manager Agent**: Operations Manager
**Worker Agents**: Partner Liaison, Compliance Agent

**Typical task flows**:

- *Partner onboarding*: Partner Liaison vets partner → Ops Manager approves (standard terms) or escalates (custom terms) → Partner Liaison completes onboarding
- *Compliance audit*: Compliance Agent runs scheduled audit → reports findings to Ops Manager → Ops Manager resolves or escalates
- *Cross-audit of payments*: Compliance Agent reviews Payment Agent's work (triggered by Product & Engineering Crew) → reports to Ops Manager → Ops Manager coordinates with Engineering Head

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

## 3. Tools Each Agent Gets

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

### Product Manager
- `search_web(query)` — research competitors, market trends, user needs
- `git_clone(repo)` — clone project repo to read codebase for spec writing

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

## 4. What CrewAI Handles vs. What You Build

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
