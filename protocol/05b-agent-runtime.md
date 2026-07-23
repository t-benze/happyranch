# Agent Runtime: Execution, Memory & Lifecycle

How agents are spawned, how they remember across sessions, and when they run.

---

## 1. Agent Execution Model

### Every agent runs as a coding-agent session

Each agent in the organization is not just an LLM call — it's a full coding-agent session that can read files, write files, run commands, search the web, and interact with APIs. The orchestrator layer decides *when* each session runs, *what context* it gets, and *how* outputs flow between them.

### Per-agent executor selection

Agents run through a configured coding-agent CLI. The runtime ships with four
built-in adapter profiles: Claude Code (`claude -p` with `--permission-mode auto`),
Codex (`codex exec --json -`), opencode (`opencode run`), and Pi (`pi -p ... --mode json`).
Any agentic CLI that can accept a prompt argument and produce structured output may
register as a custom executor profile via org configuration — the runtime validates
argv templates against supported placeholders and builds per-profile subprocess
launches generically (THR-052 seq 6 founder ruling). This gives every agent full
coding-agent capabilities: file system access, shell commands, web search, and git
operations. Executor selection is stored per workspace in `agent.yaml`, so agents
can run on different executors in the same org.

**Custom CLI result-envelope (THR-107).** Custom CLIs may opt into token metering
by emitting a versioned JSON envelope on stdout, delimited by the sentinel markers
``__HR_ENVELOPE_BEGIN__`` and ``__HR_ENVELOPE_END__``. The daemon parses the
envelope via a generic best-effort parser (``_parse_generic_cli_usage`` in
``runtime/orchestrator/executors.py``). The envelope is optional — absence
preserves existing behavior (no token accounting). The envelope schema maps 1:1 to
the ``TokenUsage`` model (``runtime/models.py:302``) with identical key names.
Token-accounting invariants (``total`` excludes cache reads, nullable tolerance,
model-null backfill to provider label) apply uniformly to envelope-reported tokens.
The full contract is documented in
``docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md``.

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
      - agent_memory/dev_agent/memory/
    permission_mode: auto
```

### Context injection via executor bootstrap docs

The orchestrator assembles each agent's context into an executor-specific bootstrap file placed in the workspace root. Claude workspaces use `CLAUDE.md`; Codex, opencode, and Pi workspaces use `AGENTS.md`. This file is regenerated at the start of every session. It includes:
- Agent system prompt (role, accountability contract)
- Relevant org charter sections
- Pointer to the agent's persistent memory store
- Task-specific brief (the actual assignment)

### Permission enforcement and callbacks

Claude workspaces have a `.claude/settings.json` that configures Claude Code's auto-allowed tools. Codex, opencode, and Pi workspaces do not use that file. Across executors, agents call back through the same single-line `happyranch ... --from-file` contract. Agents can read, write, and execute freely within their workspace and the cloned codebase, subject to the executor's sandbox mode and the orchestrator's workflow rules. Pi has no HappyRanch-managed sandbox or permission file in this integration.

### Skill materialization at session spawn

Skills — structured guidance packages that tell an agent how to perform specific
operations — are materialized into the agent's workspace on every session spawn
by `inject_managed_skills` (`workspace_adapters.py`). This runs on all four spawn
contexts (task, thread, wake, dream).

**Two sources, unioned with release-and-system-contracts-wins.** Skills come from two directories:
- **Bundled / release-shipped:** `<project_root>/runtime/skills/<slug>/` — ships
  inside the repo, read-only at runtime.
- **User-authored:** `<org_root>/skills/<slug>/` — per-org writable store for
  operator-authored custom skills (§6, THR-092).

On slug collision (a user-authored skill reuses a slug from a bundled skill
or a system-contract skill), the bundled or system-contract entry wins — a
user-authored skill can never shadow a release-shipped or system-contract
package. The protected-slug set matches the daemon catalog path
`_union_catalog` (release slugs union SYSTEM_CONTRACTS slugs).

**FAIL-CLOSED materialization.** Any error during materialization raises
immediately. A failed materialization must NOT leave a partially-populated
skills directory passing as complete. All four caller contexts (orchestrator
`run_step`, `thread_runner`, `wake_runner`, `dream_runner`) persist a
database-terminal failure and return BEFORE executor spawn — a materialization
error in any spawn path blocks the agent launch, never silently skipped.

**Version-aware effective state (v3, THR-092 Phase 3b).** After each successful
copy, a materialization event is recorded in the `skill_validation_events` store
with the materialized version. A skill is `effective` for an agent iff:
1. The agent has an `allow` eligibility rule for the skill, AND
2. The last-materialized version equals the current store version.

When a user-authored skill is edited (version bumped in the store), the OLD
materialized version stays live and functional on disk until the next spawn
re-materializes the NEW version. A failed re-validation of the edit does NOT
remove the working old version. The agent is `assigned_not_yet_effective` until
the new version lands.

**Visibility only — NO capability change.** Skills govern which guidance
playbooks an agent sees. They grant no tools, credentials, network access,
filesystem access, sandbox policy, or permission-map/allow-rule/auth changes.

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

### Task attachment materialization at session spawn (THR-109)

When a task (or an ancestor it inherits from) has file attachments, the runtime
resolves them at session spawn by walking up the `parent_task_id` chain, unioning
any `task_attachments` rows found. The durable bytes are read from the private
task-attachment store (separate from the org-wide shared artifact store) and
written into a per-task session attachment directory under the agent's workspace
(`workspace/.happyranch/attachments/<session_id>/`). An `Attachments:` block is
injected into the brief prompt naming each file, its on-disk path, size, and
content-type hint. Delivery is by-path for all executors; image perception
depends on the executor CLI's own abilities. The materialized per-session
directory is a regenerable cache — the bytes of record live in the task-attachment
private store.

**Legacy rows.** Rows with non-`NULL` `legacy_status` (e.g. `duplicate_v1`) are
included in ancestor resolution and materialization. Their `display_name` values
differ, so the collision-safe materialized filename
(`{storage_key}__{sanitized_display_name}`) produces distinct files — legitimate
duplicate legacy attachments do not overwrite each other.

### Executor abstraction

The executor interface supports multiple backends. Four built-in adapters are
provided; additional agentic CLIs can be registered as custom profiles via org
configuration (THR-052). Swapping an agent from one executor to another is a
one-line config change in `agent.yaml`.

### Executor binary-path resolution (THR-085)

At spawn time, each executor's CLI binary is resolved as follows:

1. **Absolute path** — if the `cli_path` in Settings is already absolute
   (founder-configured override), trust it as-is.
2. **Machine-local registry** — consult the per-host binary-path registry at
   `<daemon-home>/executors.json`. If the executor kind (e.g. `claude`) is
   registered, validate the stored path: it must exist and be executable.
   - **Valid** → use the stored path.
   - **Invalid (stale path)** → raise an **actionable block** that names the
     kind, the stale path, and the fix (`happyranch executor-binaries register <kind> --path <absolute-path>`). No silent
     fallback to PATH.
3. **PATH fallback** — if the kind is NOT registered, fall back to
   `shutil.which` over the current `PATH`.
   - **Found** → use the resolved path, **with a logged warning** that this
     binary was resolved from PATH and should be registered (non-silent
     fallback per invariant 3).
   - **Not found** → raise an **actionable block** naming the kind and the
     fix (`happyranch executor-binaries register <kind> --path <absolute-path>`).

The actionble block is an `ExecutorBinaryBlocked` exception (subclass of
`RuntimeError`). It always names the specific executor kind and gives the
operator a concrete command to fix it — never an opaque `rc=143` or bare
ENOENT death.

**Why a separate `executors.json` file?** The binary-path registry is
machine-local and must be writable at runtime by the `/api/v1/executor-binaries/register`
route (master-bearer-authed, for manual operator use) and by
`/api/v1/executors/runtime/register-binary` (scoped-token loopback, for
built-in agentic CLI self-registration — THR-088). Keeping it in a dedicated file under `<daemon-home>` isolates runtime
writes from `config.yaml` (which holds Settings values that may be under
version control or shared across hosts). This is distinct from the THR-052
executor profile registry (`org/config.yaml`), which describes *which*
executor kinds and capabilities exist and is org-portable.

### Bundled CLI PATH resolution (THR-085)

When the daemon is running as a PyInstaller-frozen bundle inside the Mac app,
the bundled `happyranch` CLI binary sits alongside `happyranch-daemon` inside
`Contents/Resources/daemon/`. The daemon MUST ensure that bare-name
`happyranch` invocations by agentic executors resolve to this bundled binary
— not to a stale `~/.local/bin/happyranch` left over from a previous install.

**Detection mechanism.** The ONLY signal available to the Python daemon is
PyInstaller's canonical frozen-detection flag: `getattr(sys, 'frozen', False)`
is `True` when running as the frozen bundle. (The Swift-side
`PACKAGING_MODE=bundled` environment variable is deliberately stripped by
`EnvironmentSanitizer` before the daemon child process launches, so the
Python daemon never sees it.) When frozen, `sys.executable` is the bundled
`happyranch-daemon` at `Contents/Resources/daemon/happyranch-daemon`, so
`os.path.dirname(sys.executable)` is the directory that also contains the
bundled `happyranch` CLI.

**Resolution rule.** At daemon startup, during PATH normalization:

- **Frozen (bundled Mac app):** Prepend `os.path.dirname(sys.executable)`
  (the bundled CLI directory) at the very front of the executor child's PATH,
  *before* the standard tool directories (`/opt/homebrew/bin`,
  `/usr/local/bin`, `~/.local/bin`). This ensures bare-name `happyranch`
  resolves to the bundled binary and beats any stale `~/.local/bin/happyranch`.
  The prepend is idempotent — repeated normalization does not duplicate the
  directory.
- **Not frozen (dev/headless/CI):** No change. The bundled CLI directory is
  NOT injected. PATH resolution stays exactly as today — the existing PATH
  `happyranch` (e.g. from `~/.local/bin` in `_STANDARD_TOOL_DIRS`) wins.

Because `_callee_env()` copies `os.environ` for child subprocesses, every
executor spawn inherits the normalized PATH with the bundled directory
leading when frozen.

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
│   ├── memory/                  # Per-entry store, persists across sessions (was learnings.md; LRN- ids resolve via permanent shim)
│   ├── task_history.md          # Rolling summary of last N tasks
│   └── repo/                    # Git clone of project (pulled at session start)
├── product_manager/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   ├── specs/                   # Specs PM writes accumulate here
│   └── repo/
├── dev_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   └── repo/                    # Agent works on branches here
├── payment_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   ├── proposals/               # Payment change proposals
│   └── repo/
└── ...
```

### Three layers of memory

**1. Institutional memory (knowledge base)**
Shared across all agents. Org charter, SOPs, brand guidelines, partner directory, regulatory summaries. Read-only for most agents, write access scoped per role.

**2. Agent-specific memory (memory store)**
Each agent accumulates its own operational learnings. The Content QA records "DSAL website is more reliable than MGTO for Macau visa info." The Content Writer records "always show Octopus + AlipayHK side-by-side on HK transport guides — tourists usually only know one." These files persist across sessions and are loaded as context at session start.

After each task, the orchestrator prompts the agent: "Based on this task, are there any new memory entries to record?" Responses are appended to the memory store. Over time, when the store gets long, the orchestrator periodically asks the agent to consolidate and prune it.

Entries are addressed as `MEM-NNN`. Items migrated from the prior learnings store keep a permanent `LRN-NNN` alias so historical cross-references resolve forever. The audit trail is forward-only: new events log as `log_memory_*`; historical `log_learning_*` rows are never rewritten.

**3. ~~Performance memory~~ (REMOVED 2026-05-27)**
The 30-day rolling scorecard / tier classification was removed. The audit log (implicit `review_verdict` rows after every delegated child terminates, plus completion / failure events) is sufficient for the founder to identify which agents need attention — via `happyranch audit`. The legacy `scorecards` table is no longer created on fresh DBs.

### How context gets assembled at session start

The orchestrator regenerates the bootstrap document in the agent's workspace with:

```
1. System prompt (from 02/03-system-prompts-*.md)
2. Org charter summary (from 01-org-charter.md — key sections only)
3. Pointers to persistent files (memory/, task_history.md)
4. Team health summary (generated by orchestrator)
5. Task-specific context (brief, prior drafts, QA feedback, etc.)
```

The agent's persistent files (memory entries, prior work products) are already in the workspace — the bootstrap document just references them. The orchestrator also runs `git pull` on the repo clone to ensure fresh code.

### Write-back protocol

After each session completes, the orchestrator:
1. Extracts the completion report (`completion_report.json` written by the agent)
2. Checks for new memory entries and appends to the memory store
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
Orchestrator assembles context (system prompt, memory, task brief)
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
Orchestrator extracts output, logs results, writes back memory
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

#### Agent Todos (THR-105): agent-owned scheduled work

Agent Todos are persistent Schedule records stored in the ``schedules`` SQLite
table. Each agent may own up to 20 armed schedules; the org cap is 100. Every
Schedule carries a ``normalized_brief`` (what fires) and a ``source_instruction``
(the natural-language instruction the manager originally provided, preserved
for audit/reconciliation).

**Kinds.** Two kinds are supported:

- **One-shot** — fires exactly once at a specified UTC ``fire_at`` (max 90 days
  out), then transitions to ``fired`` (terminal).
- **Weekly** — fires every week on a single weekday + HH:MM local time + timezone.
  After each fire the schedule re-arms with the next occurrence and continues
  until either the founder cancels/pauses it or it reaches its ``expires_at``
  (default 90 days from creation). Indefinite weekly schedules (``indefinite=1``,
  founder-set only) have no expiry.

**Fire mechanism.** The schedule fire is a two-stage pipeline:

1. **Scheduler (daemon loop).** A 60-second tick scans all orgs for ARMED
   Schedule rows whose ``fire_at <= now`` (one-shot) or ``fire_at`` is within a
   120-second tolerance window (weekly). For weekly rows whose ``fire_at`` is
   stale (missed during daemon downtime), the scheduler advances ``fire_at`` to
   the next weekly occurrence or expires the schedule — **no replay/backfill**
   of missed occurrences. A claimed row transitions from ARMED → FIRING.

2. **Runner + spawn callback.** The schedule worker loop drains the
   ``ScheduleQueue`` and invokes the owning agent's executor with a dedicated
   schedule-fire prompt. The agent's single job is to call the
   ``happyranch schedules spawn`` callback exactly once. The spawn callback:

   - Accepts only FIRING Schedule rows (single-use, record-scoped guard).
   - Creates one root task from the stored ``normalized_brief``, targeted to the
     owning agent on its own team.
   - Records ``spawned_task_ids`` and increments ``fire_count``.
   - Resolves the terminal state: one-shot → FIRED (terminal); weekly → re-armed
     with the next ``fire_at``, or EXPIRED if the next occurrence exceeds
     ``expires_at`` and ``indefinite=0``.
   - Writes ``schedule_spawned`` and ``schedule_completed`` audit log rows.
   - Enqueues the spawned task.

**Token usage.** Token usage for the schedule-fire executor session is recorded
under ``scope_type="schedule"`` and ``scope_id=<SCHEDULE-NNN>``, keeping it
separate from task-scoped token usage.

**Constraints.**

- The schedule's ``normalized_brief`` is the brief for the spawned root task —
  the schedule payload cannot choose the agent, team, or brief.
- Every Schedule targets a single agent on its own team (self-targeted).
- Cross-agent scheduling is not supported.
- Hidden / invisible schedules (not visible in the CLI ``list`` output) are
  not supported — every Schedule is visible to its owning agent.
- Weekly schedules never replay/backfill missed occurrences. A daemon restart
  after a missed slot advances the schedule to the next occurrence without
  enqueuing a fire job for the stale slot.
- The spawn callback is the only fire path — no alternate trigger mechanisms
  exist.

**Arming (creating) schedules.** Agents create new schedules by calling the
``happyranch schedules create`` callback — a single-line invocation that POSTs
to ``/api/v1/orgs/{slug}/schedules``:

   happyranch schedules create --org <slug> --from-file <path>

The payload file is a JSON object with ``task_id``, ``session_id``, ``agent``,
``source_instruction``, ``normalized_brief``, ``kind``, ``fire_at``, and
optionally ``recurrence`` and ``timezone``.  The server enforces:

- **Self-target only:** the creating agent is resolved server-side from the
  active session (``task_id`` + ``session_id`` + ``agent`` validated against
  the in-memory SessionTracker).  The payload cannot choose another agent.
- **Explicit instruction only:** both ``source_instruction`` (the verbatim
  NL instruction) and ``normalized_brief`` (the structured normalized brief)
  are mandatory.  Natural-language-only arming (without normalization)
  is refused.
- **Capability gate (default-deny):** the agent must be listed in
  ``scheduling.enabled_agents`` in ``org/config.yaml``.  Omission, empty
  list, and missing key all reject with 409 ``scheduling_disabled``.
- **Caps and defaults:** the 20-per-agent / 100-org-wide armed caps, 90-day
  one-shot horizon, weekly shape validation (single weekday + HH:MM + IANA
  timezone only), and 90-day recurring expiry are enforced at create time
  by the ``ScheduleService``.

Arming is fully autonomous — no pre-arming founder approval step — but the
schedule is immediately visible in the founder/operator ``list`` and ``show``
outputs and carries a ``schedule_created`` audit row with ``task_id=<SCHEDULE-NNN>``.

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
