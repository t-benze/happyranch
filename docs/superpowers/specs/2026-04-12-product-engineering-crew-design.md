# Product & Engineering Crew — Implementation Spec

Steps 1-6 of the OPC implementation: a working crew with orchestrator, audit logging, revision loop, agent memory, and performance scoring.

---

## 1. Crew Composition

| Agent | Role | Type |
|-------|------|------|
| Engineering Head | Manager — reviews, approves/rejects, routes work | Manager |
| Product Manager | Writes specs, triages bugs, prioritizes roadmap | Worker |
| Dev Agent | Implements features, fixes bugs | Worker |
| Payment Agent | Proposes payment flow changes | Worker |

Engineering Head is a new role not in the original org design. Product Manager has been moved from manager to worker. System prompts are defined in `02-system-prompts-managers.md` (Engineering Head) and `03-system-prompts-workers.md` (Product Manager, Dev Agent, Payment Agent).

---

## 2. Task Flows

### implement_feature

```
Engineering Head receives feature request
    → assigns Product Manager to write spec
    → PM delivers spec + completion report
    → Engineering Head routes spec to Dev Agent
    → Dev Agent implements + completion report
    → Engineering Head reviews implementation
    → APPROVE / REVISE (max 2 rounds) / REJECT
```

### bug_fix

```
Engineering Head receives bug report
    → assigns Product Manager to triage (severity, repro, priority)
    → PM delivers triage report + completion report
    → Engineering Head routes triage to Dev Agent
    → Dev Agent fixes + completion report
    → Engineering Head verifies fix
    → APPROVE / REVISE (max 2 rounds) / REJECT
```

### payment_change

```
Engineering Head receives payment change request
    → assigns Payment Agent to draft proposal
    → Payment Agent delivers proposal + compliance considerations + completion report
    → Orchestrator logs "cross-audit requested" (stubbed — auto-approved)
    → Engineering Head reviews proposal
    → APPROVE / REVISE (max 2 rounds) / REJECT
```

### Revision targeting

In all flows, revision targets the **worker who produced the output**. Engineering Head decides who needs to revise. After 2 revision rounds without approval, the task escalates to the founder.

---

## 3. Agent Executor

All agents run as Claude Code sessions:

```
claude -p "<task prompt>" --permission-mode auto
```

Invoked from each agent's persistent workspace directory. Claude Code reads the workspace's `CLAUDE.md` and `.claude/settings.json` automatically.

### CLAUDE.md (agent identity)

Regenerated only when the agent's identity changes (scorecard tier change, learnings consolidation, org charter update). Contains:

- System prompt (role, standards, accountability contract, performance tiers)
- Org charter summary (key sections relevant to this agent)
- Pointers to persistent files: `learnings.md`, `scorecard.md`, `recent_tasks.md`

Does NOT contain task-specific context. Tasks are passed via the `-p` prompt.

### Task prompt

Passed via `claude -p`. Contains:

- Task brief (what to do)
- Input from previous step (spec from PM, triage report, review feedback)
- Revision context if applicable (round number, max rounds, prior feedback)

### .claude/settings.json (permissions + hooks)

```json
{
  "permissions": {
    "allow": ["Read(*)", "Write(*)", "Bash(*)", "Glob(*)", "Grep(*)"]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Read|Grep|Glob",
        "command": "cd repo && git pull --ff-only 2>/dev/null; true",
        "runOnce": true
      }
    ]
  }
}
```

- `permissions.allow`: generous — all standard tools auto-approved.
- `hooks.PreToolUse`: runs `git pull` once on first tool use for agents with a repo clone.
- Agents without a repo clone (if any) omit the hook.

### Completion report

Every agent is instructed (via CLAUDE.md) to write `completion_report.json` to the workspace root at the end of each session:

```json
{
  "task_id": "TASK-001",
  "agent": "dev_agent",
  "status": "completed",
  "confidence": 85,
  "output_summary": "Implemented Alipay payment integration...",
  "risks_flagged": ["Alipay sandbox differs from production behavior"],
  "dependencies": ["Payment Agent's gateway config"],
  "suggested_reviewer_focus": ["Error handling for failed Alipay callbacks"]
}
```

Note: `duration_seconds`, `token_count`, and `estimated_cost` are populated by the orchestrator (from session metadata), not by the agent. They appear in the `task_results` database table but not in the agent's completion report.

If no report is found after the session, the orchestrator marks the task as failed and logs the event.

---

## 4. Persistent Workspaces

Each agent has a persistent workspace that survives across sessions:

```
workspaces/
├── engineering_head/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── scorecard.md
│   ├── recent_tasks.md
│   └── repo/                    (git clone, pulled via hook)
├── product_manager/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── scorecard.md
│   ├── recent_tasks.md
│   ├── specs/                   (specs PM writes accumulate)
│   └── repo/
├── dev_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── learnings.md
│   ├── scorecard.md
│   ├── recent_tasks.md
│   └── repo/                    (agent works on branches here)
└── payment_agent/
    ├── CLAUDE.md
    ├── .claude/settings.json
    ├── learnings.md
    ├── scorecard.md
    ├── recent_tasks.md
    ├── proposals/               (payment change proposals)
    └── repo/
```

### What persists

- `learnings.md` — agent appends insights during sessions. Periodically consolidated by the orchestrator.
- `scorecard.md` — updated by orchestrator after each task.
- `recent_tasks.md` — rolling summary of last N tasks, appended by orchestrator.
- Work products: specs/, proposals/, code on branches.
- `repo/` — git clone, kept fresh via PreToolUse hook.

### What gets regenerated

- `CLAUDE.md` — only on identity changes (scorecard tier, learnings consolidation, org charter update). Not per-task.
- `.claude/settings.json` — only when permission config changes.

---

## 5. Agent Memory

### During sessions

Agents write to `learnings.md` in their workspace when they discover something reusable. This is instructed in CLAUDE.md as part of the agent's role:

> "If you learn something reusable for future tasks, append it to `learnings.md` in your workspace."

### After sessions (orchestrator)

The orchestrator:
1. Reads `completion_report.json`
2. Updates `scorecard.md` with the task outcome
3. Appends a summary to `recent_tasks.md`
4. Logs to SQLite (audit_log + task_results)

### Periodic consolidation

When `learnings.md` exceeds 200 lines, the orchestrator triggers a consolidation session — a lightweight Claude Code session that reads the file, deduplicates, prunes stale entries, and rewrites it. This check runs after each task completion.

---

## 6. Permission Model

### Approach

All agents run with `--permission-mode auto`. The `.claude/settings.json` allows all standard tools. No custom permission layer.

### Founder-concern boundaries

The only restrictions that matter, enforced via system prompt + orchestrator post-session review:

| Boundary | Enforcement |
|----------|-------------|
| No `git push` to main / production deploy | System prompt + orchestrator review |
| Spend >$200 single or >$100/month recurring | System prompt → escalation |
| Raw payment card data storage (PCI-DSS) | System prompt + orchestrator review |
| Political sensitivity in content | System prompt → escalation |
| Refunds >$150 | System prompt → escalation |
| Downtime >30 minutes | System prompt → escalation |

Everything else is auto-approved.

---

## 7. Database Schema

Single SQLite file. Four tables.

### tasks

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT | Primary key, e.g. "TASK-001" |
| type | TEXT | implement_feature / bug_fix / payment_change |
| status | TEXT | pending / in_progress / completed / in_review / revise / approved / rejected / escalated |
| assigned_agent | TEXT | Current agent working on this step |
| crew | TEXT | "product_engineering" |
| brief | TEXT | Task description |
| revision_count | INTEGER | Default 0, max 2 |
| created_at | TEXT | ISO 8601 |
| updated_at | TEXT | ISO 8601 |
| completed_at | TEXT | ISO 8601, nullable |

### audit_log

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Auto-increment |
| task_id | TEXT | FK to tasks |
| agent | TEXT | Agent that performed the action |
| action | TEXT | session_start / session_end / completion_report / review_verdict / escalation |
| payload | TEXT | JSON blob |
| timestamp | TEXT | ISO 8601 |

### scorecards

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Auto-increment |
| agent | TEXT | Agent name |
| period_start | TEXT | ISO 8601 |
| period_end | TEXT | ISO 8601 |
| acceptance_rate | REAL | 0.0-1.0 |
| revision_rate | REAL | 0.0-1.0 |
| error_count | INTEGER | |
| tier | TEXT | green / yellow / red |
| updated_at | TEXT | ISO 8601 |

### task_results

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Auto-increment |
| task_id | TEXT | FK to tasks |
| agent | TEXT | Agent that produced this result |
| session_id | TEXT | Unique session identifier |
| output_summary | TEXT | |
| confidence_score | INTEGER | 0-100 |
| learnings | TEXT | Nullable |
| risks_flagged | TEXT | JSON array |
| duration_seconds | INTEGER | |
| token_count | INTEGER | |
| estimated_cost | REAL | USD |
| created_at | TEXT | ISO 8601 |

---

## 8. Orchestrator

### Entry point

Python CLI:

```
python scripts/run_product_crew.py --task implement_feature --brief "Add Alipay support for international cards"
```

### Core loop

```
1. Create task record in SQLite (status: pending)
2. Check agent scorecards → build task chain based on tiers
3. For each step in the chain:
   a. Build task prompt (brief + input from previous step + revision context)
   b. Spawn: claude -p "<prompt>" --permission-mode auto
      (from the assigned agent's workspace directory)
   c. Wait for session to complete
   d. Read completion_report.json from workspace
   e. Log to audit_log and task_results
   f. Update scorecard, append to recent_tasks.md
   g. Route output to next step (or back for revision)
4. Engineering Head reviews final output
5. APPROVE → task marked approved
   REVISE → increment revision_count, loop back to worker (max 2)
   REJECT → task marked rejected
   After 2 revisions → task marked escalated
```

### Components

| File | Responsibility |
|------|---------------|
| `orchestrator.py` | Main loop — receives requests, builds task chains, manages lifecycle |
| `executor.py` | Spawns Claude Code sessions, reads completion reports |
| `context_builder.py` | Generates CLAUDE.md and .claude/settings.json per agent (on identity changes, not per-task) |
| `task_router.py` | Determines which agent handles each step based on task type and tier |
| `revision_loop.py` | Tracks revision count, feeds back review comments, escalates at max |
| `performance_tracker.py` | Scores agents, calculates tiers, adjusts task chains |
| `audit_logger.py` | Writes to SQLite audit_log and task_results |
| `database.py` | SQLite setup, migrations, queries |

### Task chain examples (tier-dependent)

```
implement_feature (all Green):
  PM writes spec → Dev implements → Engineering Head reviews

implement_feature (Dev Agent Yellow):
  PM writes spec → Dev implements → Engineering Head pre-review → Dev revises → Engineering Head final review

implement_feature (Dev Agent Red):
  PM writes spec → Dev implements → Engineering Head pre-review → Ops Manager peer review → Engineering Head final review
```

Note: Ops Manager peer review is not available until Ops Crew is built. For now, Red tier adds an extra Engineering Head review step instead.

---

## 9. Cross-Crew Audit Stub

The `payment_change` task flow normally requires a Compliance Agent cross-audit from the Ops Crew. Until Ops Crew is built:

- The orchestrator logs `"cross_audit_requested"` in the audit_log
- The audit is auto-approved with a placeholder result
- The Engineering Head's review context includes a note: "Cross-audit stubbed — Compliance Agent review pending Ops Crew implementation"

This exercises the cross-crew communication interface so it's ready when Ops Crew comes online.

---

## 10. Out of Scope

- Feishu integration (Step 7+)
- Knowledge base / RAG (Step 8)
- Content Crew, Ops Crew, CX Crew (Steps 6-9)
- Real cross-crew audits (requires Ops Crew)
- Founder dashboard (Step 11)
- Additional executors (Codex, OpenCode)
- Persistent Support Agent
