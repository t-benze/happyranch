# Agent Runtime: Execution, Memory & Lifecycle

How agents are spawned, how they remember across sessions, and when they run.

---

## 1. Agent Execution Model

### Every agent runs as a coding-agent session

Each agent in the organization is not just an LLM call — it's a full coding-agent session that can read files, write files, run commands, search the web, and interact with APIs. The orchestrator layer decides *when* each session runs, *what context* it gets, and *how* outputs flow between them.

### Per-agent executor selection

Agents run through a configured coding-agent CLI. Today the runtime supports Claude Code (`claude -p` with `--permission-mode auto`), Codex (`codex exec --json -`), opencode (`opencode run`), and Pi (`pi -p ... --mode json`). This gives every agent full coding-agent capabilities: file system access, shell commands, web search, and git operations. Executor selection is stored per workspace in `agent.yaml`, so agents can run on different executors in the same org.

Each agent's configuration specifies context and workspace:

```
agent_config:
  dev_agent:
    executor: claude
    system_prompt: 03-system-prompts-workers.md#dev-agent
    workspace: workspaces/dev_agent/
    context_files:
      - 01-org-charter.md
      - knowledge_base/technical/
      - agent_memory/dev_agent/learnings.md
    permission_mode: auto
```

### Context injection via executor bootstrap docs

The orchestrator assembles each agent's context into an executor-specific bootstrap file placed in the workspace root. Claude workspaces use `CLAUDE.md`; Codex, opencode, and Pi workspaces use `AGENTS.md`. This file is regenerated at the start of every session. It includes:
- Agent system prompt (role, accountability contract)
- Relevant org charter sections
- Pointer to the agent's persistent learnings file
- Task-specific brief (the actual assignment)

### Permission enforcement and callbacks

Claude workspaces have a `.claude/settings.json` that configures Claude Code's auto-allowed tools. Codex, opencode, and Pi workspaces do not use that file. Across executors, agents call back through the same single-line `happyranch ... --from-file` contract. Agents can read, write, and execute freely within their workspace and the cloned codebase, subject to the executor's sandbox mode and the orchestrator's workflow rules. Pi has no HappyRanch-managed sandbox or permission file in this integration.

**Only founder-concern boundaries are restricted** (as defined in the org charter):
- No `git push` to `main` / production deploy
- No actions involving spend >$200 single or >$100/month recurring
- No raw payment card data storage (PCI-DSS)
- No publishing content touching political sensitivity

These guardrails are enforced by the agent's system prompt (in `CLAUDE.md` or `AGENTS.md`) and the orchestrator's post-session review — not by provider-specific deny rules. If an agent violates a founder-concern boundary, the orchestrator catches it and escalates.

### Full codebase access

All agents can clone the project's git repo into their workspace for read access to the full codebase. The orchestrator handles the initial `git clone` (or `git pull` if already cloned) at session start so the agent always has fresh code. Agents can also pull on their own during a session.

Write restrictions are role-based but minimal:
- Dev Agent: can create branches, commit, push to feature branches (not main)
- Payment Agent: can create branches within `src/payments/**`, push to feature branches
- Product Manager: writes specs to workspace, no code commits
- Engineering Head: reviews only, no direct code changes

### Executor abstraction

The executor interface supports multiple backends now. Claude and Codex are implemented; additional coding-agent CLIs can be added behind the same abstraction. Swapping an agent from one executor to another is a one-line config change in `agent.yaml`.

---

## 2. Agent Memory Architecture

### Problem
Coding-agent sessions are stateless — context is lost when a session ends. Agents need to remember past work and learn from experience across sessions.

### Solution: persistent workspaces with file-based memory

Every agent has a **persistent workspace directory** that survives across sessions. The workspace contains the agent's memory files, any work products it creates (specs, code, proposals), and a cloned copy of the project repo. The orchestrator regenerates the executor bootstrap file (`CLAUDE.md` or `AGENTS.md`) and Claude settings when applicable at session start, but everything else persists.

```
workspaces/
├── engineering_head/
│   ├── agent.yaml               # Includes executor + repos
│   ├── CLAUDE.md or AGENTS.md   # Regenerated each session
│   ├── .claude/settings.json    # Claude-only permission config
│   ├── learnings.md             # Persists across sessions
│   ├── task_history.md          # Rolling summary of last N tasks
│   └── repo/                    # Git clone of project (pulled at session start)
├── product_manager/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── task_history.md
│   ├── specs/                   # Specs PM writes accumulate here
│   └── repo/
├── dev_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── task_history.md
│   └── repo/                    # Agent works on branches here
├── payment_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── task_history.md
│   ├── proposals/               # Payment change proposals
│   └── repo/
└── ...
```

### Three layers of memory

**1. Institutional memory (knowledge base)**
Shared across all agents. Org charter, SOPs, brand guidelines, partner directory, regulatory summaries. Read-only for most agents, write access scoped per role.

**2. Agent-specific memory (learnings file)**
Each agent accumulates its own operational learnings. The Content QA records "DSAL website is more reliable than MGTO for Macau visa info." The Content Writer records "always show Octopus + AlipayHK side-by-side on HK transport guides — tourists usually only know one." These files persist across sessions and are loaded as context at session start.

After each task, the orchestrator prompts the agent: "Based on this task, are there any new learnings to record?" Responses are appended to the learnings file. Over time, when the file gets long, the orchestrator periodically asks the agent to consolidate and prune it.

**3. ~~Performance memory~~ (REMOVED 2026-05-27)**
The 30-day rolling scorecard / tier classification was removed. The audit log (implicit `review_verdict` rows after every delegated child terminates, plus completion / failure events) is sufficient for the founder to identify which agents need attention — via `happyranch audit`. The legacy `scorecards` table is no longer created on fresh DBs.

### How context gets assembled at session start

The orchestrator regenerates the bootstrap document in the agent's workspace with:

```
1. System prompt (from 02/03-system-prompts-*.md)
2. Org charter summary (from 01-org-charter.md — key sections only)
3. Pointers to persistent files (learnings.md, task_history.md)
4. Team health summary (generated by orchestrator)
5. Task-specific context (brief, prior drafts, QA feedback, etc.)
```

The agent's persistent files (learnings, prior work products) are already in the workspace — the bootstrap document just references them. The orchestrator also runs `git pull` on the repo clone to ensure fresh code.

### Write-back protocol

After each session completes, the orchestrator:
1. Extracts the completion report (`completion_report.json` written by the agent)
2. Checks for new learnings and appends to the learnings file
3. Writes an implicit `review_verdict` audit row for delegated work (approved / rejected) so the founder can audit per-agent outcomes via `happyranch audit`
4. Appends to `recent_tasks.md` with a summary of the task
5. Logs everything to the audit trail (SQLite)
6. Does NOT clean up the workspace — files persist for future sessions

---

## 3. Agent Lifecycle and Scheduling

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
Orchestrator assembles context (system prompt, learnings, task brief)
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

**Which agents use this mode:** Content Writer, Content QA, SEO Agent, Dev Agent, Payment Agent, QA Engineer, Partner Liaison, Compliance Agent, and all 4 Manager Agents for their review/approval tasks.

#### Mode 2: Scheduled (recurring tasks on a cron)
Some work happens on a fixed schedule. The orchestrator's scheduler triggers these sessions at configured times. The session runs, completes its task, and shuts down — same as on-demand, but the trigger is a clock instead of a task queue.

**Scheduled tasks:**

| Schedule | Agent | Task |
|---|---|---|
| Daily 9:00 AM | Content Manager | Generate and send daily report to founder |
| Daily 9:15 AM | Product Manager | Generate and send daily report |
| Daily 9:30 AM | Ops Manager | Generate and send daily report |
| Daily 9:45 AM | CX Manager | Generate and send daily report |
| Every Friday | Content QA | Content freshness audit — flag guides older than 90 days |
| Every Monday | SEO Agent | Weekly keyword ranking report |
| 1st of month | Compliance Agent | Monthly regulatory scan across 3 jurisdictions |
| 1st of month | Ops Manager | Monthly partner SLA compliance review |
| Weekly Monday 10:00 AM | Orchestrator (not an agent) | Generate and post weekly org summary to the dashboard |

Each scheduled task is configured in the orchestrator's scheduler (a cron-like system). Missed runs (e.g., Mac Mini was off) are handled by a catch-up mechanism: on startup, the orchestrator checks for missed scheduled tasks and runs them.

#### Mode 3: Persistent (Support Agent only)
The Support Agent is the one exception. Tourists need real-time help and the response time target is under 5 minutes. Two approaches:

**Option A: True persistent session.** The Support Agent runs as a long-lived agent session that waits for incoming inquiries. Advantages: instant response, no cold start. Disadvantages: continuous LLM session cost, needs health monitoring and auto-restart.

**Option B: Fast on-demand with warm-up.** The Support Agent is spun up on-demand like other agents, but with optimizations to reduce cold start: pre-assembled context kept ready, a lightweight executor for simple queries, full executor only for complex ones. If 10-20 second startup is acceptable within the 5-minute response window, this avoids the cost of a persistent session.

**Recommendation:** Start with Option B (fast on-demand). Switch to Option A only if response time is consistently too slow or if support volume justifies the cost.

### Concurrency

The orchestrator controls how many agent sessions run simultaneously. On a Mac Mini, practical limits:

| Constraint | Guideline |
|---|---|
| Concurrent sessions | 2-3 max (LLM API rate limits, memory, CPU for executors) |
| Task queuing | Tasks beyond concurrency limit are queued and processed FIFO |
| Priority queue | Tier 1 escalations and founder-initiated tasks jump the queue |
| Session timeout | 30 minutes max — if an agent session hasn't completed, kill it and escalate |

This means if the Content Writer is drafting a guide and the Content QA needs to review something else simultaneously, both can run. But if a third task arrives, it waits in the queue. The orchestrator logs queue wait times — if tasks are regularly waiting, it's a signal to either optimize agent session speed or increase concurrency.

### Cost profile

With on-demand sessions, daily cost scales with actual work, not idle time:

| Phase | Estimated daily sessions | Estimated daily LLM cost |
|---|---|---|
| Phase 1 (Content Team only) | 5-10 sessions | $3-8 |
| Phase 2 (+ Product/Ops Teams) | 15-25 sessions | $8-20 |
| Full org (all 4 Teams active) | 25-40 sessions | $15-35 |

These are rough estimates assuming Claude Sonnet pricing. Actual costs depend on task complexity, revision rounds, and which executor is used. The dashboard's cost tracking page (Page 6) gives you real-time visibility.
