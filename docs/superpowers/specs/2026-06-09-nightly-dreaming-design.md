# Nightly Dreaming - Design

> Status: current
> Current Source: This spec until implementation lands; executable truth will be `runtime/daemon/`, `runtime/orchestrator/org_config.py`, protocol dream skill docs, and tests.
> Superseded By: None
> Notes: Design approved for implementation. Dreams are private scheduled reflection runs, not tasks, talks, or threads.

## Goal

Add a nightly "dreaming" mechanism: a scheduled private self-reflection run for selected agents. A dream lets an agent review recent work since its last successful dream, extract durable private learnings, propose KB candidates for founder review, and notify the founder only when there is meaningful output.

Dreams are intentionally separate from:

- Tasks: execution and orchestration surfaces.
- Talks: founder-to-agent interactive conversations.
- Threads: founder-visible asynchronous coordination.

Dreams reuse executor infrastructure where practical, but they have their own lifecycle, persistence, scheduler, and callback contract.

## Non-Goals

- No automatic KB writes from dreams. Dreams may produce KB candidates only.
- No multi-turn internal dialogue in v1. Each dream is one reflective invocation.
- No dream rows in task lists or task metrics.
- No replay of every missed day after daemon downtime.
- No automatic thread to agents other than the founder.
- No web UI in v1 unless needed by later implementation planning.

## Org Configuration

Dreaming is opt-in per org in `<runtime>/orgs/<slug>/org/config.yaml`:

```yaml
dreaming:
  enabled: true
  schedule:
    time: "02:00"
    timezone: "Asia/Shanghai"
    catch_up_on_startup: true
  agents:
    mode: all          # all | whitelist
    include: []        # used when mode=whitelist
    exclude: []        # always subtracts from selected agents
```

Selection rules:

1. Candidate agents are approved agent files under `org/agents/*.md` with existing workspaces.
2. `mode: all` selects every candidate.
3. `mode: whitelist` selects only `include`.
4. `exclude` is applied last for both modes.
5. Unknown names in `include` or `exclude` should fail config validation so typos do not silently skip agents.

Scheduling rules:

- Schedule time is interpreted in the configured timezone using standard timezone data.
- The daemon schedules at most one dream per selected agent per local date.
- If `catch_up_on_startup` is true and today's configured time has already passed, startup may enqueue one catch-up dream for each selected agent lacking a successful dream for the local date.
- If the daemon was offline for multiple days, it does not replay historical days.

## Data Model

Add `DreamStatus`:

- `pending`
- `running`
- `completed`
- `failed`
- `timeout`
- `skipped`

Add a `dreams` table:

```sql
CREATE TABLE dreams (
    id TEXT PRIMARY KEY,                  -- DREAM-NNN
    agent_name TEXT NOT NULL,
    local_date TEXT NOT NULL,             -- YYYY-MM-DD in configured timezone
    scheduled_for TEXT NOT NULL,          -- ISO timestamp
    window_start TEXT,
    window_end TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    summary TEXT,
    transcript_path TEXT,
    new_learnings_count INTEGER NOT NULL DEFAULT 0,
    kb_candidate_count INTEGER NOT NULL DEFAULT 0,
    founder_thread_id TEXT,
    session_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(agent_name, local_date)
);
```

Add a `dream_kb_candidates` table:

```sql
CREATE TABLE dream_kb_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dream_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT NOT NULL,
    rationale TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | promoted | rejected | superseded
    promoted_kb_slug TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(dream_id, slug),
    FOREIGN KEY (dream_id) REFERENCES dreams(id)
);
```

Dream transcripts live at:

```text
<runtime>/orgs/<slug>/dreams/DREAM-NNN.md
```

The transcript file includes frontmatter with dream id, agent, window, status, learning count, KB candidate count, and optional founder thread id, followed by private summary and transcript.

## Input Window

Each dream uses:

- `window_start`: the prior successful dream's `ended_at` for that agent.
- fallback `window_start`: 24 hours before `window_end` when there is no prior successful dream.
- `window_end`: the dream row creation time.

Inputs should include:

- the agent's normal bootstrap context
- task history since `window_start`
- audit rows involving the agent since `window_start`
- learnings added or updated since `window_start`
- recent talk/thread/task followups involving the agent when available
- lightweight org/team context needed to interpret the window

The prompt must state that this is private reflection, not a task. The agent should inspect patterns, contradictions, recurring friction, stale assumptions, and candidate improvements.

## Invocation Contract

Each dream is one executor invocation. It should complete by calling a new callback command with a file payload, for example:

```bash
happyranch dreams complete --org <slug> --dream-id DREAM-001 --from-file /tmp/dream-result-DREAM-001.json
```

Payload shape:

```json
{
  "summary": "private markdown summary",
  "learnings": [
    {
      "slug": "short-id",
      "title": "Durable private lesson",
      "topic": "workflow",
      "body": "..."
    }
  ],
  "kb_candidates": [
    {
      "slug": "candidate-slug",
      "title": "Possible org-wide rule",
      "topic": "operations",
      "rationale": "Why this may belong in KB",
      "body_path": "/tmp/dream-kb-candidate-slug.md"
    }
  ],
  "founder_thread": {
    "needed": true,
    "subject": "Nightly reflection: agent_name",
    "body_markdown": "Short founder-visible summary with candidates/actions"
  }
}
```

The CLI reads `--from-file`, expands each `kb_candidates[].body_path` into `body_markdown`, and sends the expanded request to the daemon. The daemon should not read arbitrary body paths itself.

Validation:

- `summary` is required.
- Learning fields are required and bounded to reasonable sizes.
- KB candidate metadata fields are required.
- `body_path` must be read by the CLI and converted to `body_markdown` before the HTTP request.
- `founder_thread.needed=false` permits empty subject/body.
- `founder_thread.needed=true` requires non-empty subject and body.

## Completion Effects

On successful callback:

1. Write the private dream transcript.
2. Append structured per-agent learnings to the existing `learnings/` store. Use `source_task: DREAM-NNN` for provenance even though dreams are not tasks.
3. Store KB candidates in `dream_kb_candidates`.
4. If `founder_thread.needed=true`, create a thread addressed only to `@founder`.
5. Mark the dream `completed`.
6. Audit dream completion with counts and optional founder thread id.

The founder thread body should mention candidate ids/slugs and summarize why attention is needed. It must not add other agent recipients automatically. If another agent should be looped in, the thread can recommend that action to the founder.

## Scheduler

Add a daemon `dream_scheduler_loop` started during FastAPI lifespan startup and cancelled during shutdown. It runs alongside task queue workers, thread workers, Feishu listeners, and jobs recovery hooks.

Loop behavior:

1. Every minute, load current org config for each loaded org.
2. Skip orgs where dreaming is disabled.
3. Resolve selected agents.
4. For each selected agent, compute the configured local date and scheduled timestamp.
5. If scheduled time has passed and no dream row exists for `(agent, local_date)`, insert a pending dream and enqueue it.

Startup behavior:

- During lifespan startup, run the same scheduling check once after orgs are loaded and DB recovery has run.
- This catches today's missed run only when `catch_up_on_startup=true`.

Queue behavior:

- Add a `DreamQueue` and a small worker pool, or a single worker for v1.
- Dream workers run `DreamRunner`.
- A per-agent lock prevents two dream invocations for the same agent in the same org from running concurrently.

## Failure Handling

- Executor failure: mark `failed`, preserve error summary, do not advance the successful-dream window.
- Timeout: mark `timeout`, preserve timeout error, do not advance the successful-dream window.
- Missing callback: mark `failed` or `timeout` using the same timeout boundary as invocation timeout.
- Callback validation failure: mark `failed`, preserve validation error, do not write learnings or candidates.
- Daemon restart while running: startup recovery marks stale `running` dreams as `failed` with reason `daemon_restart`.
- Duplicate scheduling: DB uniqueness on `(agent_name, local_date)` is authoritative.

If a dream fails for today's date, the unique row prevents repeated automatic attempts in the same day unless the implementation adds an explicit retry command. This avoids runaway nightly retry loops.

## Founder Surfaces

V1 founder-facing CLI/API:

```bash
happyranch dreams status --org <slug> [--agent <name>]
happyranch dreams list   --org <slug> [--agent <name>] [--limit N]
happyranch dreams show   --org <slug> DREAM-001 [--json]
```

Potential later CLI/API:

```bash
happyranch dreams candidates list --org <slug> [--agent <name>]
happyranch dreams candidates promote --org <slug> <candidate-id> --kb-slug <slug>
happyranch dreams candidates reject --org <slug> <candidate-id> --reason <why>
```

Promotion can be a follow-up feature. V1 only needs to persist and show candidates clearly.

Dashboard integration is not required in v1, but audit rows should make future dashboard cards straightforward.

## Audit And Token Usage

Audit actions should include:

- `dream_scheduled`
- `dream_started`
- `dream_completed`
- `dream_failed`
- `dream_timeout`
- `dream_founder_thread_created`

Token usage should record dream attribution explicitly. Extend token usage scope values to include `dream` and populate `scope_id` with `DREAM-NNN`. Do not overload `task_id`.

## Security And Permissions

The dream callback must follow the existing single-line `happyranch ... --from-file <path>` convention.

Dream invocations should allow only the baseline `happyranch` CLI side-effect channel plus ordinary read/write access inside the agent workspace. Dreams should not get special permissions to edit org config, write KB directly, or dispatch other agents.

KB candidate body files are read by the local CLI process from paths supplied in the payload. The daemon receives only expanded markdown. This avoids a daemon endpoint that reads arbitrary filesystem paths.

## OpenAPI And Web Contract

Every browser-callable route added for founder-facing dream list/show/candidate APIs must have a TypeScript mirror under `web/src/lib/api/` or be explicitly excluded with justification. OpenAPI snapshot changes must be intentional and regenerated through the existing contract test.

Agent callback routes, if not browser-callable, should be treated like existing callback routes and documented accordingly.

## Test Plan

Unit tests:

- `OrgConfig` parses valid `dreaming:` config.
- Invalid schedule time, timezone, mode, and unknown agent names fail clearly.
- Agent selection handles all, whitelist, include, and exclude.
- Scheduler inserts at most one dream per `(agent, local_date)`.
- Startup catch-up runs only today's missed dream and never replays old dates.
- Last-successful-dream window calculation falls back to 24 hours.
- Dream callback validation rejects malformed payloads.
- KB candidate `body_path` expansion happens in CLI, not daemon.

Daemon/route tests:

- Dream completion writes transcript, learnings, candidate rows, audit rows, and status.
- `founder_thread.needed=true` creates a founder-only thread.
- `founder_thread.needed=false` does not create a thread.
- Failed and timed-out dreams do not advance the successful window.
- Startup recovery marks stale running dreams failed.

Integration tests:

- Fake executor completes a dream through `happyranch dreams complete`.
- Nightly scheduler enqueues a dream under controlled time.
- Token usage rows use dream scope.

## Implementation Notes

Likely new modules:

- `runtime/daemon/dream_queue.py`
- `runtime/daemon/dream_runner.py`
- `runtime/daemon/dream_scheduler.py`
- `runtime/daemon/routes/dreams.py`
- `runtime/infrastructure/dream_store.py`
- `protocol/skills/dream/SKILL.md`
- `cli/commands/dreams.py`

Likely modified modules:

- `runtime/orchestrator/org_config.py`
- `runtime/infrastructure/database.py`
- `runtime/infrastructure/audit_logger.py`
- `runtime/daemon/app.py`
- `runtime/daemon/state.py`
- `runtime/models.py`
- `cli/main.py`
- OpenAPI and web API mirrors for founder-facing routes

The implementation should avoid coupling dreams to `TaskRecord`, thread invocation purposes, or talk lifecycle state. Dreams may reuse shared lower-level executor helpers, token parsing, workspace bootstrap assembly, and learnings store code.
