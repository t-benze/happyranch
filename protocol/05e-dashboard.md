# Founder Dashboard & Implementation Order

The self-hosted dashboard for org-wide visibility, plus the recommended build sequence.

---

## 1. Dashboard Purpose

The Feishu chats are for real-time interaction — conversations, approvals, daily updates. The dashboard is for when you want the big picture: how is the org performing, what happened recently, where are the trends going, and what's the full audit trail. It runs as a local web app on your Mac Mini, accessible from any device on your network.

### Fully Self-Hosted
All data stays on your Mac Mini — no third-party observability service. The orchestrator records execution data (agent decisions, task timelines, tool usage, LLM calls) directly to SQLite alongside the business-level metrics (scorecards, escalation history, calibration insights). One dashboard covers both engineering observability and business performance — no cloud dependency, no data leaving your infrastructure, full compliance with PIPL/PDPO/PDPA.

### Tech Stack
- **Backend**: FastAPI (Python) — reads from the same SQLite database the orchestrator writes to
- **Frontend**: Lightweight React app (or plain HTML + Chart.js for simplicity)
- **Hosting**: Runs on your Mac Mini alongside the orchestrator, served on a local port (e.g., `http://mac-mini.local:8080`)
- **Auth**: Simple token or password gate (this is a local network app, not public-facing)
- **Refresh**: Auto-refreshes every 60 seconds, or manual refresh
- **Observability**: Orchestrator hooks (session_start / session_end / orchestration_step / completion_report audit events) → SQLite (no external tracing service needed). Optionally add OpenTelemetry with a self-hosted Jaeger/Grafana if you want richer execution traces later

---

## 2. Dashboard Layout

### Page 1: Live Status

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
│  │ Content Team                                         ││
│  │   ● Content Writer: Drafting "Shenzhen day trip"     ││
│  │   ○ QA Agent: Idle (waiting for draft)               ││
│  │   ○ SEO Agent: Idle                                  ││
│  │                                                      ││
│  │ Product Team                                         ││
│  │   ● Dev Agent: Implementing mobile perf fixes        ││
│  │   ○ Payment Agent: Idle                              ││
│  │                                                      ││
│  │ Ops Team                                             ││
│  │   ● Partner Liaison: Vetting Macau hotel candidates  ││
│  │   ○ Compliance Agent: Idle                           ││
│  │                                                      ││
│  │ CX Team                                              ││
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

### Page 2: Agent Scorecards

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

### Page 3: Work Log (Audit Trail)

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

### Page 4: Escalation History

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

### Page 5: Trends

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

### Page 6: Execution Traces

Engineering-level observability for debugging and cost tracking — fully self-hosted, no third-party tracing service.

```
┌──────────────────────────────────────────────────────────┐
│  Execution Traces                      [Today ▼]        │
│  Filter: [Team ▼] [Agent ▼] [Status ▼] [Search...]     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  COST SUMMARY (today)                                    │
│  Total LLM calls: 47  |  Total tokens: 182,400          │
│  Input: 134,200  |  Output: 48,200                      │
│  Estimated cost: $4.82                                   │
│                                                          │
│  RECENT CREW RUNS                                        │
│  ─────────────────────────────────────────────────────   │
│  ▶ RUN-087  Content Team  14:32  ✅ 3m 22s  $0.84      │
│    ├── Content Writer  write_content     2m 10s  $0.52  │
│    │   └── LLM: 3 calls, 12,400 tokens                 │
│    │   └── Tools: search_web (2x), check_source (1x)   │
│    ├── QA Agent  qa_review               0m 48s  $0.24  │
│    │   └── LLM: 2 calls, 5,200 tokens                  │
│    │   └── Tools: check_url (4x), check_exchange_rate   │
│    └── Content Manager  manager_review   0m 24s  $0.08  │
│        └── LLM: 1 call, 2,100 tokens                   │
│                                                          │
│  ▶ RUN-086  CX Team  13:15  ✅ 1m 05s  $0.31           │
│    ├── Support Agent  handle_inquiry     0m 52s  $0.28  │
│    └── CX Manager  (not invoked — resolved by agent)    │
│                                                          │
│  ▶ RUN-085  Content Team  12:00  ⚠ 5m 44s  $1.62       │
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

Clicking a run expands the full trace: every LLM call with input/output, tool invocations with arguments and results, timing breakdown, token counts, and the executor used (Claude Code, Codex, etc.). This is captured via the orchestrator's audit hooks (`session_start`, `session_end`, `orchestration_step`, `completion_report`) and logged to SQLite.

Data sources: orchestrator audit log (session/step/completion events), LLM usage tracking

---

## 3. Dashboard API Endpoints

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
| `GET /api/traces?team=&date=` | Execution traces with LLM calls, tool usage, timing |
| `GET /api/traces/{run_id}` | Full trace detail for a specific team run |
| `GET /api/costs?period=7d` | LLM cost breakdown by agent, team, and time period |

### Connection to Feishu

The dashboard and Feishu are two views of the same data. Quick commands in Feishu (`status`, `health`, `scorecard`) hit the same API endpoints as the dashboard. The "Pending Your Action" items on the dashboard link to the corresponding Feishu group chat thread where you can approve/deny. The dashboard is read-only — all actions (approvals, directives, goal-setting) happen through Feishu conversations.

---

## 4. Suggested Implementation Order

1. **Content Team only** — the simplest, most self-contained unit. Content Writer + QA Agent + Content Manager with hierarchical process. Get the basic write → review → approve flow working.

2. **Agent executor abstraction** — build the layer that can spawn a coding-agent session (start with one provider, e.g., Claude Code) with the right context files. This is the foundation for everything else.

3. **Add audit logging** — wrap each agent session with callbacks that log every task start, completion, and review to your audit store.

4. **Add the revision loop** — orchestrator logic to re-run when QA returns REVISE, with revision count tracking and escalation after max rounds.

5. **Add agent memory** — implement the learnings file write-back, scorecard injection, and periodic consolidation.

6. **Add performance scoring** — after each Team run, score the agents. Start displaying scorecards. Implement tier-based task chain adjustment.

7. **Add Feishu bot integration** — set up the OPC Hub app bot and 4 manager webhook bots. Create the 7 group chats. Implement daily reports (Tier 3), standard approvals (Tier 2), reply parsing, and the decision-routing loop. Test with the Content Manager group chat first.

8. **Add the knowledge base** — RAG access to org charter, guides, and SOPs. Scoped read/write per agent.

9. **Stand up Team 2 (Product) and Team 3 (Ops)** — these are more complex due to cross-team dependencies (Compliance Agent cross-auditing Payment Agent).

10. **Add inter-Team communication** — the orchestrator routes cross-team tasks.

11. **Stand up CX Team** — with the Support Agent as a persistent session for real-time chat (running outside the standard task loop) while still reporting into the CX Team for review workflows.

12. **Add additional executor support** — once the abstraction is solid with one provider, add Codex and OpenCode as options.

13. **Build the founder dashboard** — FastAPI backend exposing the REST API, React or static HTML frontend with the 6 pages (live status, scorecards, work log, escalation history, trends, execution traces). Connect the Feishu quick commands to the same API endpoints. No third-party observability dependency — all data captured via orchestrator audit hooks and stored locally.
