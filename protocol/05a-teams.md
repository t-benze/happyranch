# Teams, Agents & Tools

How the org design maps to the runtime (Python daemon + Claude Code agent sessions + SQLite) and what each agent can do.

---

## 1. Concept Mapping

| Your Org Concept | Runtime Primitive | Notes |
|---|---|---|
| Worker Agent (e.g., Content Writer) | Persistent agent workspace + Claude Code session | `<runtime>/workspaces/<agent>/` holds CLAUDE.md, settings, skills, repos. Each task spawns a headless `claude -p` session against that workspace |
| Manager Agent (e.g., Content Manager) | Same as a worker, but with an EH-style orchestration prompt | Managers decide at each step: handle, delegate, escalate. Engineering Head is the first manager implemented; others follow the same pattern |
| Content task (e.g., "write Macau visa guide") | `TaskRecord` row + brief | Tasks have a type hint (`implement_feature`, `bug_fix`, `payment_change`, `general`) that steers the manager's decision, not a hardcoded chain |
| QA review of that content | Second agent session triggered by the manager's orchestration decision | Maker-checker preserved — the manager delegates to a different agent via the `delegate` action |
| Manager approval step | Manager's final `done` action after reviewing the worker's completion report | Captured as a `verdict` audit entry |
| Functional team (Content Writer + QA + Content Mgr) | Group of agent workspaces plus the `team` field on each task | Team is a taxonomy, not a scheduling unit — the orchestrator doesn't instantiate "a team", it just spawns the agents the manager picks |
| Peer audit (cross-manager review) | Cross-team task spawned by the orchestrator per escalation rules | Routed between managers in the orchestrator layer |
| Escalation to founder | Manager returns `{action: "escalate", reason: "..."}` from a decision session | The orchestrator surfaces the escalation and the founder resolves it via `opc resolve-escalation` |
| Knowledge base | File-backed markdown under `<runtime>/kb/` | Any agent reads; any agent writes via `opc kb add --from-file`; engineering_head deletes; founder records precedents |
| Audit logger | Semantic events in SQLite (`session_start`, `completion_report`, `verdict`, `escalation`, `orchestration_step`, etc.) | Wired into every orchestrator action and every agent callback |
| Performance scoring | Rolling 30-day scorecard (green/yellow/red) surfaced to the manager in its capabilities prompt | Tier feedback shapes the manager's delegation decisions naturally |

---

## 2. Team Definitions

### Team 1: Content Team

**Manager Agent**: Content Manager
**Worker Agents**: Content Writer, SEO Agent, Content QA

**Typical task flow**:
1. Content Manager receives a brief (from editorial calendar or CX feedback)
2. Content Manager delegates writing task to Content Writer
3. Content Writer produces draft with completion report
4. Content Manager routes draft to Content QA for review
5. Content QA returns verdict: PASS / REVISE / REJECT
6. If REVISE → back to Content Writer with specific issues (max 2 rounds, then escalate)
7. If REJECT → Content Manager escalates to founder
8. If PASS → Content Manager makes final approval decision
9. SEO Agent reviews metadata/schema for approved content (parallel or post-approval)

**Tasks owned by this team**:

- `write_content`: Content Writer. Input = brief + content type + audience. Output = draft with sources cited and completion report.
- `qa_review`: Content QA. Input = draft from write_content (passed in the delegate prompt). Output = structured verdict with checklist, issues, suggestions, and completion report.
- `seo_optimize`: SEO Agent. Input = approved content. Output = title tag, meta description, schema markup recommendations, internal linking suggestions.

**Revision loop**: If Content QA returns REVISE, the manager's next orchestration step delegates back to Content Writer with the revision feedback injected into the prompt. Revision count is tracked on the `TaskRecord`; after two rounds the manager escalates.

---

### Team 2: Product & Engineering Team

**Manager Agent**: Engineering Head
**Worker Agents**: Product Manager, Dev Agent, Payment Agent, QA Engineer

**Typical task flows**:

- *Feature development*: Engineering Head receives feature request → assigns Product Manager to write spec → routes spec to Dev Agent for implementation → routes diff to QA Engineer for verification → Engineering Head reviews the QA verdict and reconciles with the spec
- *Bug fix*: Engineering Head receives bug report → assigns Product Manager to triage (severity, repro, priority) → routes triage to Dev Agent for fix → routes fix to QA Engineer for regression + acceptance testing → Engineering Head verifies
- *Payment flow change*: Engineering Head assigns Payment Agent to draft proposal → cross-audit requested (stubbed until Ops Team is built — auto-approved) → QA Engineer exercises the updated flow end-to-end → Engineering Head reviews proposal + QA verdict

**Revision loop**: In all flows, the revision targets the worker who produced the output (Dev Agent, Payment Agent, or Product Manager as appropriate). QA Engineer returns PASS / REVISE / BLOCK; REVISE loops back to the relevant producer. Engineering Head decides who needs to revise. Max 2 rounds before escalation to founder.

**Tasks owned by this team**:

- `implement_feature`: Product Manager writes spec (input = feature request), then Dev Agent implements (input = spec). Output = implementation description, test results, deployment readiness, completion report.
- `bug_fix`: Product Manager triages (input = bug report), then Dev Agent fixes (input = triage report). Output = fix description, root cause, test verification, completion report.
- `payment_change`: Payment Agent. Input = change request. Output = change proposal with compliance considerations, completion report. **Note**: This task would normally trigger a cross-team audit from the Compliance Agent. Until Ops Team is built, the cross-audit is stubbed (logged and auto-approved).
- `qa_verify`: QA Engineer. Input = diff or change proposal plus the originating spec. Output = PASS / REVISE / BLOCK verdict with test-run logs, regression notes, and coverage-gap list. Completion report.

---

### Team 3: Operations Team

**Manager Agent**: Operations Manager
**Worker Agents**: Partner Liaison, Compliance Agent

**Typical task flows**:

- *Partner onboarding*: Partner Liaison vets partner → Ops Manager approves (standard terms) or escalates (custom terms) → Partner Liaison completes onboarding
- *Compliance audit*: Compliance Agent runs scheduled audit → reports findings to Ops Manager → Ops Manager resolves or escalates
- *Cross-audit of payments*: Compliance Agent reviews Payment Agent's work (triggered by Product & Engineering Team) → reports to Ops Manager → Ops Manager coordinates with Engineering Head

**Tasks owned by this team**:

- `vet_partner`: Partner Liaison. Input = partner info. Output = vetting report (licenses, ratings, API capability, insurance), completion report.
- `onboard_partner`: Partner Liaison. Input = approved partner + terms. Output = onboarding checklist completion, API credentials obtained, commission rate set, completion report.
- `compliance_audit`: Compliance Agent. Input = audit scope (jurisdiction, domain). Output = findings with severity, regulation references, recommended actions, completion report.
- `cross_audit_payment`: Compliance Agent. Input = payment change proposal from Product Team. Output = compliance verdict with specific regulation references, completion report.

---

### Team 4: CX Team

**Manager Agent**: CX Manager
**Worker Agents**: Support Agent

**Typical task flows**:

- *Tourist support*: Support Agent handles inquiry → resolves or escalates to CX Manager
- *Refund request*: Support Agent documents request → CX Manager approves (≤$150) or escalates to founder (>$150)
- *Feedback loop*: Support Agent identifies recurring issue → CX Manager creates ticket for Content Team or Product Team

**Tasks owned by this team**:

- `handle_inquiry`: Support Agent. Input = tourist message + booking context. Output = response + resolution status, completion report.
- `process_refund`: Support Agent. Input = refund request details. Output = documented justification + tourist interaction history, submitted to CX Manager for approval, completion report.
- `create_feedback_ticket`: Support Agent. Input = pattern of recurring issues. Output = structured feedback ticket with data, suggested improvements, target team, completion report.

**Note**: The CX Team is the most real-time facing. In practice, the Support Agent may run as a persistent agent (always-on) rather than spun up per task. The current runtime is batch-oriented — treat CX as a future extension where the Support Agent runs outside the standard task loop and only reports back into the CX Team for review workflows.

---

## 3. Tools Each Agent Gets

Agents running as Claude Code sessions have native access to file system, shell, and web. The tools below are *additional* capabilities the orchestrator exposes to each agent — either as `opc` CLI subcommands reachable through the `Bash(opc:*)` allow rule or as future MCP tools.

### Shared tools (all agents)
- `read_knowledge_base(topic)` — query the org charter, SOPs, brand guidelines (currently: `opc kb list/get/search`)
- `submit_completion_report(report)` — mandatory after every task (currently: `opc report-completion --from-file`)
- `escalate(category, severity, summary)` — trigger the escalation router (currently: set `status: escalated` in the completion report)
- `view_team_health()` — see the current team health summary
- `record_learning(insight)` — append to agent's learnings file (currently: `opc learning`)

### Content Writer
- `search_web(query)` — research destinations, verify facts
- `check_source(url)` — verify an official source is current

### Content QA
- `search_web(query)` — verify claims against official sources
- `check_url(url)` — test if a link is live
- `check_exchange_rate(from, to)` — verify currency conversions

### QA Engineer
- `run_tests(scope)` — execute unit/integration/E2E suites against the change
- `check_performance(url)` — measure page load and API latency against the budget
- `diff_coverage(base, head)` — report coverage delta and missing branches
- `exercise_flow(flow_name)` — drive booking/payment/i18n flows end-to-end

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

## 4. Runtime Responsibilities

Unlike frameworks that bake in task chaining, revision loops, and manager-delegation semantics, the runtime here is hand-rolled Python — which means the orchestrator owns all of it explicitly. This is deliberate: the manager agent (Engineering Head today, others later) decides each step dynamically rather than following a static task graph.

**The orchestrator owns:**
- Spawning `claude -p` subprocess sessions per agent workspace (see `src/orchestrator/executor.py`)
- Running the EH-driven decision loop (ask manager → execute `delegate` / `done` / `escalate` → feed the result back as history)
- Agent workspace provisioning: CLAUDE.md, `.claude/settings.json`, copied skills, repo clones (`src/orchestrator/context_builder.py`)
- Task state machine (`pending` → `in_progress` → `completed`/`rejected`/`escalated`) and the 10-step runaway guard
- Founder interaction surface (Feishu bot + dashboard — both still planned; today the CLI + SSE stream covers it)
- Inter-team task routing (e.g., Product → Ops cross-audit)
- Escalation resolution (`opc resolve-escalation`) and precedent recording (`opc kb precedent --as-founder`)
- Revision tracking (`revision_count` on `TaskRecord`; manager escalates after 2 rounds)
- Performance scoring and tier management (`src/orchestrator/performance_tracker.py`)
- Audit logging via semantic actions written to SQLite (`src/infrastructure/audit_logger.py`)
- Knowledge base with scoped access (`src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py`)
- Real-time support (future: Support Agent as a persistent session rather than per-task spawn)

**What lives inside the agent workspace** (not the orchestrator):
- The agent's identity and role prompt (`CLAUDE.md`)
- Skills that codify procedure (`start-task`, `make-worktree`, `manage-repo`, `manage-agent`)
- Learnings, scorecard, and task history files
- Agent-scoped repos

**Areas to revisit:**
- **Real-time support**: the batch task loop doesn't fit live chat. Support Agent likely needs a persistent session outside the standard orchestrator loop, reporting into the CX Team only for review workflows
- **Complex state machines**: if revision loops or cross-team dependencies get hairy, consider an explicit state-machine layer (LangGraph-style) for the orchestrator rather than the current imperative loop
