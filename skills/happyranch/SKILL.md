---
name: happyranch
description: Manage the HappyRanch AI tourism organization via the `happyranch` CLI — submit tasks, stream events, inspect audit logs, approve agent enrollments, operate the shared knowledge base, manage threads (compose/send/invite/archive), and record founder-resolved precedents. Use when the user asks about HappyRanch tasks, agents, threads, the daemon/runtime, the knowledge base, or the one-person-company system.
metadata:
  {
    "openclaw":
      {
        "emoji": "🏢",
        "requires": { "bins": ["uv"] },
      },
  }
---

# HappyRanch

Manage the HappyRanch (one-person company) AI tourism organization: submit tasks to the manager-driven orchestrator, watch them stream, handle the agent enrollment flow, recall past task context, and operate the shared knowledge base.

> Commands below use the skill-local shim `scripts/happyranch`, which auto-detects the project root from the skill's own location and invokes `uv --project <root> run happyranch`. The skill source lives at `<project>/skills/happyranch/`; the shim uses `cd -P` so it resolves back to the real checkout. Override with `HAPPYRANCH_PROJECT_DIR` if the skill is ever relocated outside the project tree.

## Prerequisites

- HappyRanch project checked out — the shim walks `scripts/ → happyranch/ → skills/ → project root` from its own real path
- `uv sync` run once in the project (creates `.venv/bin/happyranch`)
- Daemon running:
  ```bash
  <project>/scripts/daemon.sh start          # pid/port under ~/.happyranch/
  <project>/scripts/daemon.sh status         # or stop
  ```
- An active runtime container — `scripts/happyranch init <path>` to create + register + activate a slugless container; `scripts/happyranch use <path>` to switch.
- At least one org inside it — `scripts/happyranch orgs init <slug> [--from examples/orgs/hk-macau-tourism]` materializes per-org content under `<runtime>/orgs/<slug>/`.

## Org selection

In a multi-org container every per-org command needs a slug. Resolution order:

1. Explicit `--org <slug>` on the command line
2. `HAPPYRANCH_ORG_SLUG` env var (export once per shell — most ergonomic)
3. Auto-infer (only when exactly one org exists in the container)

Container-level commands (`init`, `use`, `orgs ...`) take no `--org`.

Examples below assume `HAPPYRANCH_ORG_SLUG` is set; if it isn't, append `--org <slug>` to each per-org call.

## Tasks

```bash
# Submit a task — the team manager decides the approach; CLI returns immediately.
scripts/happyranch run --brief "Explore how the payment module handles refunds"

# Multi-line brief from a file (mutually exclusive with --brief)
scripts/happyranch run --brief-file /tmp/brief.md

# Route to a team
scripts/happyranch run --team engineering --brief "Add Alipay for international cards"
scripts/happyranch run --team content     --brief "Write a Macau visa walkthrough"

# Reattach to a running (or historical) task and stream its events
scripts/happyranch tail TASK-001

# Snapshot: status, block_kind, note, results, last event, audit summary
scripts/happyranch details TASK-001
scripts/happyranch details TASK-001 --full   # untruncated per-step output summaries
# Task status is one of {pending, in_progress, blocked, completed, failed}.
# When status=blocked the block_kind is either `delegated` (waiting on child
# tasks) or `escalated` (waiting on founder). `note` carries the human-readable
# reason or the founder's resolution rationale.

# Recent tasks (default 20)
scripts/happyranch tasks
scripts/happyranch tasks --limit 50

# Recall — fetch a past task's brief, final summary, and (optionally) output files
scripts/happyranch recall TASK-001                              # brief + final summary
scripts/happyranch recall TASK-001 --tree                       # include the full subtree of child tasks
scripts/happyranch recall TASK-001 --fetch-output               # inline output file bodies (capped at ~200KB)

# Revisit — founder-initiated: spawn a NEW root task that inherits the brief of a terminal predecessor.
# TTY-gated; no --yes bypass; prompts for confirmation before POSTing.
scripts/happyranch revisit TASK-052 [--note "founder hint" | --note-file PATH] [--session-timeout-seconds N]

# Cancel a running task (founder). SIGTERMs live subprocesses and cascades down the subtree.
scripts/happyranch cancel TASK-052 [--rationale "..."] [--no-cascade]
# --no-cascade cancels only this row and leaves children parentless — dangerous; default cascades.

# Per-session token usage (input/output/cache_read/cache_creation/reasoning)
scripts/happyranch tokens                                       # most recent sessions
scripts/happyranch tokens --task-id TASK-001
scripts/happyranch tokens --agent engineering_head
scripts/happyranch tokens --since 2026-05-01                    # ISO date or full timestamp
scripts/happyranch tokens --by-agent                            # rollup per agent
scripts/happyranch tokens --by-task                             # rollup per task
scripts/happyranch tokens --json                                # raw JSON for piping
```

## Agents

```bash
# Initialize or refresh workspaces (CLAUDE.md, settings, skills, repo clones)
scripts/happyranch init-agent                  # all agents
scripts/happyranch init-agent dev_agent        # specific agent

# Enrollment flow (founder-gated) — the founder-side counterpart to the
# agent-callback `manage-agent` subcommand.
scripts/happyranch enrollments                                  # all enrollments
scripts/happyranch enrollments --status pending                 # one of {pending,approved,rejected,terminated}
scripts/happyranch approve-agent content_writer
scripts/happyranch reject-agent  content_writer

# Per-agent repos (founder-direct; agents usually go through manage-repo skill)
scripts/happyranch manage-repo add    --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs.git
scripts/happyranch manage-repo remove --agent dev_agent --repo-name docs
scripts/happyranch manage-repo update --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs-v2.git
```

## Knowledge Base

Per-org entries under `<runtime>/orgs/<slug>/kb/` — each org has its own KB; orgs do not share. Full rules: `protocol/06-knowledge-base.md`.

```bash
# Read (safe, any agent / any caller)
scripts/happyranch kb list [--topic <t>] [--type <label>]      # `type` is a freeform string label
scripts/happyranch kb get <slug>
scripts/happyranch kb search "<terms>"

# Write (any agent, --from-file only — multi-line happyranch invocations are blocked
# by the Bash(happyranch:*) permission matcher, so a file is mandatory)
scripts/happyranch kb add    --agent <you> --from-file /tmp/kb-<slug>.md
scripts/happyranch kb add    --agent <you> --from-file /tmp/kb-<slug>.md --force-new-sibling
# --force-new-sibling bypasses the near-duplicate (similar title/tags) check
# returned as 409 near_duplicate. Use when the daemon's match is a false
# positive and you genuinely want a second sibling entry.
scripts/happyranch kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md

# Delete — team manager (audited); founder may override with --as-founder
scripts/happyranch kb delete <slug> --agent <you> --confirm [--as-founder]

# Regenerate _index.md (usually unnecessary — happens automatically after every write)
scripts/happyranch kb reindex
```

`kb add` / `kb update` payload files use YAML frontmatter (`slug`, `title`, `type`, `topic`, optional `tags`, `source_task`) followed by a markdown body. There is no separate `kb precedent` subcommand any longer — founder rulings flow through plain `kb add` with `source_task: <task-id>` in the frontmatter so the link back to the escalation is preserved.

## Shared Artifacts

Org-wide blob store at `<runtime>/orgs/<slug>/artifacts/`. Flat directory (no nesting in v1) for persistent artifacts produced by agents — reports, exports, screenshots, PDFs. Files survive across tasks and are visible to every agent in the org.

```bash
scripts/happyranch artifacts put <local-path> --agent <you> [--name <name>] [--org <slug>]
scripts/happyranch artifacts list [--org <slug>]
scripts/happyranch artifacts get <name> --output <local-path> [--org <slug>]
```

- Names match `[A-Za-z0-9._-]+`, max 200 chars; slash-bearing names rejected.
- Size cap: 10 MB per file. Larger uploads return HTTP 413.
- `put` is idempotent (overwrites by default). No version history.
- `put` is audited (`action="artifact_put"`); `list` and `get` are not.
- All access goes through `happyranch` — direct filesystem writes don't work uniformly across Claude/Codex/Opencode executors (sandboxes block writes outside the agent workspace).
- Not the KB. KB is for typed knowledge (slug + frontmatter); artifacts are opaque blobs.

## Founder Escalation Resolution

```bash
# State transition — founder approves or rejects an escalated task with a rationale.
# Task ends up in status=completed (approve) or status=failed (reject); block_kind cleared.
scripts/happyranch resolve-escalation --task-id TASK-N --decision approve|reject --rationale "…"

# Then (if the decision is worth preserving as a precedent), write a normal KB entry
# with source_task pointing back at TASK-N. Example payload at /tmp/kb-<slug>.md:
#
#   ---
#   slug: refund-grace-peak-season
#   title: Refund grace period during peak season
#   type: precedent
#   topic: refund-policy
#   source_task: TASK-N
#   tags: [refunds, peak-season]
#   ---
#
#   Body: the binding ruling, why it was reached, and any caveats.
scripts/happyranch kb add --agent founder --from-file /tmp/kb-<slug>.md
```

`--agent` on `kb add` / `kb update` is metadata (stamped as `authored_by`) — the daemon does not validate it against the team registry. By convention, founder-authored entries use `--agent founder` so future readers can tell the entry came from a human ruling rather than an agent. Bearer-token auth controls *whether* the call succeeds.

## Talks

1:1 founder↔agent conversation flow (TALK-NNN). Talks are the simplest interactive surface — use them for quick Q&A, brainstorming, or dispatching a task with full conversational context. End-of-talk residue lands in the agent's learnings (or legacy `learnings.md`) automatically. Use threads (below) instead when 2+ agents need to participate.

```bash
# Start a new talk with one agent (returns TALK-NNN + first agent response)
scripts/happyranch talk start --agent engineering_head

# Resume an existing talk
scripts/happyranch talk resume --talk-id TALK-007

# List / inspect
scripts/happyranch talk status                     # open talks only
scripts/happyranch talk status --agent payment_agent
scripts/happyranch talk list [--agent <name>] [--limit 50]
scripts/happyranch talk show TALK-007              # transcript
scripts/happyranch talk show TALK-007 --json       # raw JSON

# Abandon an open talk (founder; frozen with reason, no end-of-talk residue)
scripts/happyranch talk abandon --talk-id TALK-007 --reason "superseded by TALK-008"
```

`happyranch talk end --talk-id TALK-NNN --from-file ...` is the **agent-side** end-of-talk callback — the founder does not run it directly. Dispatching a task from inside a talk also goes through the agent (`happyranch dispatch`).

## Threads

Email-style multi-agent workchannels (THR-NNN). The founder composes, agents reply/decline/self-dispatch into them, and the founder can archive or later resume them. The web UI is the primary founder surface; `scripts/happyranch threads` with no subcommand is only a compatibility stub that points to `happyranch web`.

```bash
# Browse
scripts/happyranch threads list                                          # default 50, all statuses
scripts/happyranch threads list --status open                            # status ∈ open|archived
scripts/happyranch threads list --limit 100
scripts/happyranch threads show THR-001                                  # metadata + full transcript
scripts/happyranch threads show THR-001 --json                           # raw JSON for piping

# Compose a new thread (founder only)
scripts/happyranch threads compose \
    --subject "Refund-policy review for high-season packages" \
    --recipients "engineering_head,payment_agent,legal_agent" \
    --body "Reviewing whether our cancellation grace period needs to flex during peak season. See KB:refund-policy-baseline."

# Follow-up message in an existing thread — body comes from a JSON file
# /tmp/thread-send-THR-001.json:
#   {"body_markdown": "Updated context: …"}
scripts/happyranch threads send --thread-id THR-001 --from-file /tmp/thread-send-THR-001.json

# Invite a new participant mid-thread (founder only; system message is posted)
scripts/happyranch threads invite --thread-id THR-001 --agent qa_engineer

# Raise the turn cap when a thread is hitting the limit
scripts/happyranch threads extend --thread-id THR-001 --new-cap 50

# Archive synchronously. Writes transcript and flips status to archived.
# /tmp/thread-archive-THR-001.json:
#   {"summary": "Decision: extend grace to 14 days in peak…"}
scripts/happyranch threads archive --thread-id THR-001 --from-file /tmp/thread-archive-THR-001.json

# Reopen an archived thread
scripts/happyranch threads resume --thread-id THR-001

# Forward a finished talk or thread into a NEW thread (decision continuity)
scripts/happyranch threads forward --source TALK-042 --recipients "engineering_head,product_manager" \
    [--subject "Follow-up: pricing API contract"] [--note-file /tmp/note.md]
scripts/happyranch threads forward --source THR-001 --recipients "qa_engineer"
```

Valid recipient names = active agents in `<runtime>/orgs/<slug>/org/agents/*.md` that ALSO have a workspace under `<runtime>/orgs/<slug>/workspaces/`. The daemon returns `404 {"code": "unknown_agent", "agent": "<name>"}` for any mismatch (typo, terminated agent, pending-approval agent without an `.md`). The CLI prints the same JSON, and the web UI surfaces the error.

`happyranch threads reply | decline | dispatch` are **agent-side** callbacks driven by the `thread` skill inside an invocation — the founder does not run them directly.

## Jobs

Agents who hit a permission wall or need a long-running subprocess can submit a job.

```bash
# List pending jobs (optionally filter by status, agent, or task)
scripts/happyranch jobs list [--status pending|all|...] [--agent <name>] [--task <task-id>]

# Show details, rationale, script body, and output if terminal
scripts/happyranch jobs show JOB-NNN

# TTY-gated run with live SSE stream of stdout/stderr
scripts/happyranch jobs run JOB-NNN [--cwd <path>] [--timeout-seconds <int>]

# Reject a review-required job with a reason (prompts for reason if omitted)
scripts/happyranch jobs reject JOB-NNN [--reason <text>]

# Fetch captured output (stdout, stderr, or both); --max-bytes caps the read tail
scripts/happyranch jobs output JOB-NNN [--stream stdout|stderr|both] [--max-bytes <int>]

# Agent/founder inspection helpers
scripts/happyranch jobs tail JOB-NNN [--stream stdout|stderr] [--lines 50]
scripts/happyranch jobs wait JOB-NNN [--timeout-seconds 30]
scripts/happyranch jobs stop JOB-NNN
```

Jobs run inside the daemon process with the daemon's inherited `os.environ`. If you rotate credentials interactively, restart the daemon so the new env is picked up. `scripts/happyranch scripts ...` remains as a deprecated alias for older automation, but new work should use `jobs`.

## Audit Log

```bash
scripts/happyranch audit TASK-007                                  # full entries for a task
scripts/happyranch audit --agent engineering_head --limit 10       # recent for one agent, any task
scripts/happyranch audit TASK-007 --action orchestration_step      # only EH decisions
scripts/happyranch audit --since 2026-04-18T00:00:00Z              # time-filtered
scripts/happyranch audit TASK-007 --json                           # raw JSON (full payloads)
```

Common action values: `session_start`, `session_end`, `completion_report`, `orchestration_step`, `escalation`, `escalation_resolved`, `verdict`.

## Runtime

A **runtime container** is a slugless folder that hosts one or more **orgs**. One daemon serves every org in the active container concurrently.

```bash
# Container level — create + register + activate a new container.
scripts/happyranch init /path/to/runtime

# Switch which container the daemon serves.
scripts/happyranch use  /path/to/runtime

# Inside the active container: list / create / detach orgs.
scripts/happyranch orgs                                                  # alias for: happyranch orgs list
scripts/happyranch orgs init <slug> --from examples/orgs/hk-macau-tourism
scripts/happyranch orgs unload <slug>                                    # drops daemon state; does NOT delete files
```

Per-org content lives under `<runtime>/orgs/<slug>/`:

- `org/{charter.md, escalation-rules.md, teams.yaml, config.yaml, agents/*.md}` — editable org definition
- `workspaces/<agent>/` — one workspace per approved agent (CLAUDE.md or AGENTS.md, repos, learnings)
- `kb/`, `talks/`, `threads/` — per-org content stores
- `happyranch.db` — per-org SQLite

Migration commands are TTY-gated and refuse to run if active tasks or open talks exist.

## Common Workflows

**Submit + watch**
```bash
scripts/happyranch run --brief "…"                              # submits and returns immediately
scripts/happyranch run --brief-file /tmp/brief.md               # for multi-line briefs
scripts/happyranch tail TASK-001                                # attach to live events (Ctrl-C detaches)
```

**Pick up context on a past task**
```bash
scripts/happyranch recall TASK-012                              # brief + completion summary
scripts/happyranch recall TASK-012 --tree                       # include the full subtree of child tasks
scripts/happyranch recall TASK-012 --fetch-output               # inline output file bodies (capped at ~200KB)
```

**Diagnose a failed task**
```bash
scripts/happyranch details TASK-007
scripts/happyranch audit   TASK-007 --json | jq '.[] | select(.action == "escalation")'
```

**Onboard a new agent proposed by the EH**
```bash
scripts/happyranch enrollments --status pending
scripts/happyranch approve-agent <name>        # bootstraps workspace + clones repos
```

**Record a founder-resolved precedent**
```bash
scripts/happyranch resolve-escalation --task-id TASK-N --decision approve|reject --rationale "…"
# Then write a normal KB entry with source_task: TASK-N in the frontmatter:
scripts/happyranch kb add --agent founder --from-file /tmp/kb-<slug>.md
```

**Add a new org to the active container**
```bash
scripts/happyranch orgs init <slug> --from examples/orgs/hk-macau-tourism
export HAPPYRANCH_ORG_SLUG=<slug>                              # so subsequent commands default to it
scripts/happyranch init-agent                                   # bootstrap workspaces
```

## Safety Rules

- **Safe (no confirmation):** `run`, `tail`, `details`, `tasks`, `tokens`, `recall`, `audit`, `enrollments`, `init-agent`, `orgs`, `kb list`, `kb get`, `kb search`, `kb reindex`, `threads list`, `threads show`, `talk status`, `talk list`, `talk show`, `jobs list`, `jobs show`, `jobs output`, `jobs tail`, `jobs wait`
- **Confirm with user first:**
  - `use` — changes which container the daemon serves (affects all subsequent commands)
  - `orgs unload` — detaches an org from the daemon (files remain, but live state is dropped)
  - `approve-agent` / `reject-agent` — irreversible enrollment state changes
  - `manage-repo remove` / `manage-repo update` — mutates agent workspace config
  - `kb add` / `kb update` — writes to shared KB (visible to every agent in that org; hard to un-ring)
  - `kb add --force-new-sibling` — bypasses near-duplicate detection; only after reviewing the candidates the daemon returned
  - `kb delete` — destructive; team manager only by default, `--as-founder` overrides
  - `resolve-escalation` — founder state transition; usually paired with a follow-up `kb add` for precedents
  - `revisit` — founder-initiated spawn of a new root task from a terminal predecessor (TTY-gated CLI; agent sessions cannot invoke it)
  - `cancel` — SIGTERMs live subprocesses and cascades cancellation down the subtree
  - `cancel --no-cascade` — extra dangerous; leaves live children parentless
  - `talk start` / `talk resume` / `talk abandon` — opens or terminates a founder↔agent conversation (agent invocation triggered)
  - `threads compose` / `threads send` / `threads invite` / `threads extend` — visible to participants and triggers agent invocations
  - `threads archive` / `threads resume` / `threads forward` — visible thread state transitions / new-thread spawn
  - `jobs run` — TTY-gated; executes the job body inside the daemon process with the daemon's env
  - `jobs reject` / `jobs stop` — terminal or interrupting transition for a job
- **Agent-callback subcommands — do NOT invoke by hand:**
  - `report-completion`, `progress`, `learning {add,update,promote,reindex}`, `manage-agent`, `manage-repo`, `dispatch`, `talk end`, `threads {reply,decline,dispatch}`, `jobs submit`
  - These run inside an agent session under the `Bash(happyranch:*)` allow rule. Invoking them manually falsifies audit data and review-verdict rows. Read-side verbs (learning `list|get|search`, `jobs list|show|output|tail|wait`, `talk list|show|status`) are safe for ad-hoc inspection.

## Troubleshooting

- **`Connection refused` on any command** → daemon not running. Start it: `<project>/scripts/daemon.sh start`.
- **`no active runtime`** → `scripts/happyranch use <path>` (or `scripts/happyranch init <path>` if creating a new container).
- **`missing org slug` / `ambiguous org`** → either set `export HAPPYRANCH_ORG_SLUG=<slug>` or pass `--org <slug>`. Auto-infer only works when the container has exactly one org.
- **Task silently ends as `failed`** → likely a blocked agent callback. Check `scripts/happyranch audit <id>` for the `session_end` event; the project's CLAUDE.md explains the `Bash(happyranch:*)` allowlist requirement and the single-line `--from-file` convention.
- **Task sits in `blocked(delegated)` forever** → a child task hasn't finished or its terminal event never arrived. `scripts/happyranch tasks` shows children; drill in with `scripts/happyranch details <child>`. The parent auto-resumes when the last child terminates.
- **Task is in `blocked(escalated)`** → waiting on founder resolution. Read the `note` field via `scripts/happyranch details <id>`, then use the resolve-escalation flow above.
- **`compose failed: HTTP 404: {"code":"unknown_agent","agent":"..."}`** → the recipient is either a typo, a terminated agent, or a pending-approval agent without an active `.md` in `<runtime>/orgs/<slug>/org/agents/`. Confirm names in the web UI or under `<runtime>/orgs/<slug>/org/agents/`, then re-try.
- **`No such file or directory`** / `uv: command not found` → install `uv` and ensure the project root resolution is working (set `HAPPYRANCH_PROJECT_DIR` if the skill is in an unusual location). Shim calls `uv --project` under the hood; nothing else is expected on PATH.
