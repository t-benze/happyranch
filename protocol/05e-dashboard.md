# Founder Dashboard & Implementation Order

The self-hosted dashboard for org-wide visibility, plus the recommended build sequence.

---

## 1. Dashboard Purpose

The CLI (`grassland ...`) and SSE task streams are for real-time interaction — submitting work, streaming events, founder↔agent talks, approving escalations. The dashboard is for when you want the big picture: how is the org performing, what happened recently, where are the trends going, and what's the full audit trail. It runs as a local web app on your Mac Mini, accessible from any device on your network.

### Fully Self-Hosted
All data stays on your Mac Mini — no third-party observability service. The orchestrator records execution data (agent decisions, task timelines, tool usage, LLM calls) directly to SQLite alongside the business-level metrics (escalation history, audit trail). One dashboard covers both engineering observability and business performance — no cloud dependency, no data leaving your infrastructure. Compliance posture (e.g., applicable data-protection regimes) is defined per runtime in the org charter, not in this blueprint.

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
│  Grassland Dashboard — Live Status                    [Refresh] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  SYSTEM HEALTH          PENDING YOUR ACTION              │
│  ┌────────────────┐     ┌──────────────────────────────┐ │
│  │ ● All systems  │     │ 🟡 Refund $280 (CX Mgr)     │ │
│  │   operational  │     │    waiting 3h — run          │ │
│  │                │     │    grassland resolve-escalation     │ │
│  │ Uptime: 99.7%  │     │                              │ │
│  │ Last incident: │     │ 🟡 Partner custom terms       │ │
│  │   3 days ago   │     │    (Ops Mgr) waiting 1h      │ │
│  └────────────────┘     └──────────────────────────────┘ │
│                                                          │
│  ACTIVE TASKS BY TEAM                                    │
│  ┌──────────────────────────────────────────────────────┐│
│  │ Content Team                                         ││
│  │   ● Content Writer: Drafting a destination guide   ││
│  │   ○ Content QA: Idle (waiting for draft)               ││
│  │   ○ SEO Agent: Idle                                  ││
│  │                                                      ││
│  │ Product Team                                         ││
│  │   ● Dev Agent: Implementing mobile perf fixes        ││
│  │   ○ Payment Agent: Idle                              ││
│  │                                                      ││
│  │ Ops Team                                             ││
│  │   ● Partner Liaison: Vetting hotel candidates        ││
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

### Page 2: Work Log (Audit Trail)

Searchable, filterable log of every action in the system.

```
┌──────────────────────────────────────────────────────────┐
│  Work Log                                                │
│  Filter: [Agent ▼] [Date range] [Type ▼] [Search...]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Apr 11, 14:32  Content Writer  wrote_content            │
│    Task: Destination guide (first draft)                 │
│    Confidence: 82                                        │
│    Status: Submitted for QA review                       │
│                                                          │
│  Apr 11, 14:15  Content QA  reviewed_content_pass          │
│    Task: Visa guide (v2)                           │
│    Verdict: PASS                                         │
│    Checklist: 10/10 items verified                       │
│                                                          │
│  Apr 11, 13:50  Content Manager  decided_approve         │
│    Task: Visa guide (v2)                           │
│    Decision: Approved for publication                    │
│                                                          │
│  Apr 11, 12:00  CX Manager  escalated                   │
│    Task: Refund request $280                             │
│    Reason: Above $150 threshold                          │
│    Status: Waiting for founder approval                  │
│                                                          │
│  Apr 11, 09:00  Content Manager  daily_report            │
│    Channel: dashboard                                    │
│                                                          │
│  [Load more...]                                          │
│                                                          │
│  Showing 50 of 312 entries this week                     │
└──────────────────────────────────────────────────────────┘
```

Each entry is expandable to show the full completion report, including risks flagged, dependencies, and suggested reviewer focus. Every entry links back to the original task.

Data sources: audit logger (JSONL files or SQLite audit table)

### Page 3: Escalation History

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

### Page 4: Trends

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
│  │   90%|  ●·····●·····●          Content QA             ││
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

Data sources: all databases (task history, escalation logs, audit trail)

### Page 5: Execution Traces

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
│  RECENT TEAM RUNS                                        │
│  ─────────────────────────────────────────────────────   │
│  ▶ RUN-087  Content Team  14:32  ✅ 3m 22s  $0.84      │
│    ├── Content Writer  write_content     2m 10s  $0.52  │
│    │   └── LLM: 3 calls, 12,400 tokens                 │
│    │   └── Tools: WebFetch (2x), Read (3x)             │
│    ├── Content QA  qa_review               0m 48s  $0.24  │
│    │   └── LLM: 2 calls, 5,200 tokens                  │
│    │   └── Tools: WebFetch (4x), Grep (2x)             │
│    └── Content Manager  manager_review   0m 24s  $0.08  │
│        └── LLM: 1 call, 2,100 tokens                   │
│                                                          │
│  ▶ RUN-086  CX Team  13:15  ✅ 1m 05s  $0.31           │
│    ├── Support Agent  handle_inquiry     0m 52s  $0.28  │
│    └── CX Manager  (not invoked — resolved by agent)    │
│                                                          │
│  ▶ RUN-085  Content Team  12:00  ⚠ 5m 44s  $1.62       │
│    ├── Content Writer  write_content     2m 30s  $0.58  │
│    ├── Content QA  qa_review  → REVISE     0m 55s  $0.26  │
│    ├── Content Writer  revision_1        1m 45s  $0.52  │
│    └── Content QA  qa_review  → PASS       0m 34s  $0.18  │
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

The dashboard backend exposes a REST API that the frontend consumes. This same API could be used by other tools (scripts, CLI lookups, etc.):

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Live system status, active tasks, blocked tasks, pending approvals |
| `GET /api/logs?agent=&date=&type=` | Filtered audit trail entries |
| `GET /api/logs/{task_id}` | Full history for a specific task |
| `GET /api/escalations?period=` | Escalation history with calibration insights |
| `GET /api/trends?period=7d` | Aggregated metrics for charts |
| `GET /api/health` | Team health summary |
| `GET /api/traces?team=&date=` | Execution traces with LLM calls, tool usage, timing |
| `GET /api/traces/{run_id}` | Full trace detail for a specific team run |
| `GET /api/costs?period=7d` | LLM cost breakdown by agent, team, and time period |

### Dashboard is read-only

The dashboard is read-only. All founder actions (approvals, directives, goal-setting, rejections) happen through CLI commands (`grassland resolve-escalation`, `grassland kb add`, `grassland revisit`) or `grassland talk` conversations. "Pending Your Action" items link to the command you'd run; the dashboard never mutates state itself.

---

## 4. Suggested Implementation Order

1. **Content Team only** — the simplest, most self-contained unit. Content Writer + Content QA + Content Manager with hierarchical process. Get the basic write → review → approve flow working.

2. **Agent executor abstraction** — build the layer that can spawn a coding-agent session (start with one provider, e.g., Claude Code) with the right context files. This is the foundation for everything else.

3. **Add audit logging** — wrap each agent session with callbacks that log every task start, completion, and review to your audit store.

4. **Add the revision loop** — orchestrator logic to re-run when QA returns REVISE, with revision count tracking and escalation after max rounds.

5. **Add agent memory** — implement the learnings file write-back and periodic consolidation.

6. **Add the knowledge base** — RAG access to org charter, guides, and SOPs. Scoped read/write per agent.

7. **Stand up Team 2 (Product) and Team 3 (Ops)** — these are more complex due to cross-team dependencies (Compliance Agent cross-auditing Payment Agent).

8. **Add inter-Team communication** — the orchestrator routes cross-team tasks.

9. **Stand up CX Team** — with the Support Agent as a persistent session for real-time chat (running outside the standard task loop) while still reporting into the CX Team for review workflows.

10. **Add additional executor support** — once the abstraction is solid with one provider, add Codex and OpenCode as options.

11. **Build the founder dashboard** — FastAPI backend exposing the REST API, React or static HTML frontend with the 5 pages (live status, work log, escalation history, trends, execution traces). No third-party observability dependency — all data captured via orchestrator audit hooks and stored locally.
