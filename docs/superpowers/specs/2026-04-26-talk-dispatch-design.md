# talk-dispatch: dispatch a task from inside an open talk

## Summary

Let an agent in an open talk submit a new task to the orchestrator without ending the talk. The founder is co-present in the talk, so the dispatch is an extension of the conversation: the agent typing it, the founder bearing the authority. The task lands in the orchestrator's queue exactly like one submitted via `opc run`, with the addition of a back-pointer to the originating talk for observability.

This is the `manage-agent` precedent generalized: that endpoint already accepts talk-path auth so an EH can enroll/update/terminate agents mid-talk. `dispatch` does the same for task creation, with stricter assignment rules so workers can't escape their authority lane just because the founder is in the room.

The endpoint lives on the talks router as a talk lifecycle action (`POST /talks/{talk_id}/dispatch`), parallel to `/abandon` and `/end` — the talk_id is the authority bearer and belongs in the URL.

## Authority model

The talk is the unit of authority. While a talk is OPEN, its agent has co-presence with the founder; that grants the agent permission to create one task per `dispatch` call, with the constraints below.

### Who can dispatch

Any agent in an OPEN talk. There is no role gate on the dispatcher — the gate is "the talk exists and the founder is co-present." Workers, team managers, and any future agent type all qualify.

### Assignment rules

| Dispatcher role | Can target | Default if `target_agent` omitted |
|-----------------|------------|-----------------------------------|
| Worker          | Self only  | Self                              |
| Team manager    | Any agent in their team (including self) | Self |

Cross-team dispatch is forbidden in all cases. A worker dispatching to another worker, or a manager dispatching to an agent on another team, is rejected with 403.

The "worker self-dispatches" rule means the resulting task has `assigned_agent = dispatcher` and **bypasses the team manager's EH decision step**. The conversation in the talk is treated as the decision context; the orchestrator picks the task up and runs the worker directly.

### Why this differs from `opc run`

`opc run` always assigns a fresh task to the team manager, who then runs an EH decision step (`delegate` / `done` / `escalate`). That preserves the manager's gatekeeping role for unsolicited founder requests.

Dispatch from a talk has already done the gatekeeping in the conversation: the founder and agent jointly decided this task should exist. So:

- A worker dispatching to themselves is *equivalent to* the founder asking the team manager to assign that task to that worker, but without the round trip.
- A manager dispatching to a team worker is the same shortcut, with the manager standing in for both decision-maker and team-routing authority.

Either way, the resulting task is a root task (`parent_task_id = NULL`), not a delegated child of an existing task.

## HTTP API

### `POST /talks/{talk_id}/dispatch`

Bearer-token gated. The talk_id in the path identifies the dispatcher and bears the authority.

**Request body:**
```json
{
  "brief": "string, required, non-empty",
  "target_agent": "string, optional",
  "team": "string, optional"
}
```

**Validation order (each step gates the next):**

1. Talk exists. → 404 `not_found`.
2. Talk status is `open`. → 400 `talk_not_open`.
3. Brief is non-empty after `strip()`. → 422.
4. Resolve `dispatcher = talk.agent_name`. Resolve `dispatcher_team`:
   - First: `state.teams.team_for_manager(dispatcher)` — returns team if dispatcher is a registered team manager.
   - Else: a new helper `state.teams.team_for_agent(dispatcher)` — returns team if dispatcher is a registered worker on any team.
   - If both return None → 403 `dispatcher_team_unknown`.
5. Resolve `effective_team = body.team or dispatcher_team`. Reject if `effective_team != dispatcher_team` → 403 `cross_team_dispatch_forbidden`.
6. Resolve `effective_target = body.target_agent or dispatcher`.
7. **Worker rule:** if `state.teams.is_team_manager(dispatcher)` is False AND `effective_target != dispatcher` → 403 `worker_must_self_dispatch`.
8. **Manager rule:** if `state.teams.is_team_manager(dispatcher)` is True, then `effective_target` must be a member of `effective_team` (the manager themselves OR a worker on that team). Else → 403 `target_not_in_team`.
9. Target agent is registered: enrollment row exists with `status='approved'` AND `<runtime>/workspaces/<target>/` exists. Else → 404 `unknown_agent`.

**Insert + enqueue (under `state.db_lock`):**

- Allocate `task_id = state.db.next_task_id()`.
- Insert `TaskRecord(id=task_id, brief=body.brief, team=effective_team, assigned_agent=effective_target, dispatched_from_talk_id=talk_id)`.
- Log `task_dispatched` audit entry on the new task.
- Outside the lock: `enqueue_task(state, task_id)`.

**Response (200):**
```json
{
  "task_id": "TASK-NNN",
  "team": "engineering",
  "assigned_agent": "dev_agent",
  "dispatched_from_talk_id": "TALK-NNN"
}
```

### Read paths

- `GET /tasks/{task_id}` — `task` payload includes `dispatched_from_talk_id` (added to TaskRecord).
- `GET /audit?task_id=...` — `task_dispatched` action surfaces in the filtered audit view.
- `opc details TASK-NNN` — surfaces `Dispatched from: TALK-NNN` line when the column is set.

## Data model

### Schema change: new column on `tasks`

```sql
ALTER TABLE tasks ADD COLUMN dispatched_from_talk_id TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_talk_id
    ON tasks(dispatched_from_talk_id)
    WHERE dispatched_from_talk_id IS NOT NULL;
```

Idempotent ALTER on daemon startup, mirroring how `revisit_of_task_id` was added (see `protocol/05c-orchestrator.md` and the revisit spec). Pre-existing rows stay NULL — no backfill.

The partial index is small (only dispatched-from-talk rows have a value) and keeps `WHERE dispatched_from_talk_id = ?` lookups cheap if we later add CLI surfaces like "list tasks dispatched from this talk."

### TaskRecord change

Add `dispatched_from_talk_id: str | None = None` to `src/models.py:TaskRecord`. Like `revisit_of_task_id`, it's a sideways reference — orchestrator code that walks parent/child lineage MUST NOT follow it.

### Audit entry

New action `task_dispatched`, scoped to the **new task** (not the talk — the audit_log table is task-scoped today and growing dual-scope semantics for one feature isn't worth it).

Payload:
```json
{
  "talk_id": "TALK-NNN",
  "dispatcher_agent": "dev_agent",
  "dispatcher_role": "worker",
  "effective_target": "dev_agent",
  "team": "engineering"
}
```

`dispatcher_role` is `"worker"` or `"manager"` — the value of `state.teams.is_team_manager(dispatcher)` at dispatch time, frozen for audit.

### Transcript

The talk skill already requires the agent to record `manage-agent` calls in `transcript_markdown` so the founder sees them at talk-end. Add the same instruction for `dispatch`. The transcript is the human-readable record; the audit row + column is the queryable record. They coexist.

## CLI

### New subcommand: `opc dispatch`

```bash
opc dispatch --from-file /tmp/dispatch-<talk_id>.json
```

File-based to satisfy the Bash permission matcher's single-line invocation rule (same constraint that forces `manage-agent` and `report-completion` through `--from-file`). Multi-line bash with `\` continuations is rejected by Claude's headless permission matcher.

**Payload file:**
```json
{
  "talk_id": "TALK-NNN",
  "brief": "Implement Option B for TASK-087: change trigger to 2-hop join through guide_days.",
  "target_agent": "dev_agent",
  "team": "engineering"
}
```

`target_agent` and `team` are optional and default per the rules in §HTTP API.

The CLI is a thin client over `POST /talks/{talk_id}/dispatch`: read the file, POST it, print the response. Error responses are passed through with their HTTP code and `detail` payload.

### `opc details` rendering

When a task has `dispatched_from_talk_id` set, the existing details renderer in `src/cli.py:cmd_details` adds a single line:

```
Dispatched from: TALK-NNN  (dispatcher: dev_agent / worker)
```

The dispatcher fields come from the `task_dispatched` audit row payload.

### CLAUDE.md + README

Add `opc dispatch --from-file ...` to the agent-callbacks section of CLAUDE.md and the equivalent block in README.md. No other docs change.

## Skill updates

### `protocol/skills/talk/SKILL.md`

The existing carve-out for `manage-agent` (line 125 in current SKILL.md) lists allowed callbacks during a talk. Add a sibling line for `opc dispatch`:

> **Exception:** `opc dispatch` (create a new task) is allowed during a talk via the talk-path payload. See the `dispatch` skill. Record any such call in `transcript_markdown` so the founder has a human-readable record at talk-end. Worker agents can only dispatch to themselves; team managers can dispatch to any agent in their team.

### New skill: `protocol/skills/dispatch/SKILL.md`

Mirror the structure of `protocol/skills/manage-agent/SKILL.md`. Sections:

1. **When to use** — only inside an open talk; only after the founder has agreed to the new task in conversation.
2. **Payload** — JSON schema (talk_id, brief, optional target_agent, optional team).
3. **Authorization rules** — worker self-only; manager intra-team; cross-team forbidden.
4. **Workflow** — write the file, run `opc dispatch --from-file <path>`, record the call in `transcript_markdown`.
5. **Error handling** — list the 400/403/404 codes the agent should expect and what each means.

The skill is copied into Claude workspaces by `ClaudeWorkspaceAdapter` like the others (Codex workspaces get the equivalent inlined into AGENTS.md by `CodexWorkspaceAdapter`, same as the existing manage-agent treatment).

## Error model

| HTTP | Code | Cause |
|------|------|-------|
| 404 | `not_found` | `talk_id` does not exist |
| 400 | `talk_not_open` | talk is `closed` or `abandoned` |
| 422 | (pydantic) | missing `brief`, body validation failure |
| 403 | `dispatcher_team_unknown` | dispatcher's `talk.agent_name` is not in any team registry (orphaned agent or test fixture) |
| 403 | `cross_team_dispatch_forbidden` | `body.team` is set and differs from dispatcher's team |
| 403 | `worker_must_self_dispatch` | dispatcher is a worker AND `target_agent` is not the dispatcher |
| 403 | `target_not_in_team` | dispatcher is a manager AND `target_agent` is not a member of `effective_team` |
| 404 | `unknown_agent` | resolved `effective_target` has no workspace |

All error responses use the existing `detail = {"code": "...", ...}` shape that the rest of the daemon uses.

## Testing strategy

### Unit tests (`tests/daemon/test_talks_dispatch.py` — new file)

Parameterized validator test covering each error path above plus the happy paths:

- ✅ Worker self-dispatch with all defaults
- ✅ Manager dispatch to self with all defaults
- ✅ Manager dispatch to a team worker (explicit `target_agent`)
- ❌ Talk closed → 400 `talk_not_open`
- ❌ Talk not found → 404 `not_found`
- ❌ Empty brief → 422
- ❌ Worker targets another agent → 403 `worker_must_self_dispatch`
- ❌ Manager targets out-of-team agent → 403 `target_not_in_team`
- ❌ `body.team` differs from dispatcher's team → 403 `cross_team_dispatch_forbidden`
- ❌ Dispatcher has no team registration → 403 `dispatcher_team_unknown`
- ❌ Target workspace missing → 404 `unknown_agent`

### Persistence tests (extend `tests/test_database.py`)

- TaskRecord round-trips `dispatched_from_talk_id` through insert + read.
- Idempotent migration: ALTER applied twice does not error.
- Index is queryable (`SELECT * FROM tasks WHERE dispatched_from_talk_id = ?`).

### Route tests (extend `tests/daemon/test_talks_routes.py` or new test file)

- Successful dispatch enqueues the task (verify via `state.queue` mock).
- `task_dispatched` audit row written with correct payload.
- Response shape matches the contract in §HTTP API.

### CLI test (extend `tests/test_cli.py`)

- `opc dispatch --from-file` exits 0 on 200 response.
- Bad JSON → exit 1 with stderr error.
- Daemon error → exit 1 with HTTP code + `detail` printed.
- `opc details` renders the `Dispatched from:` line when the column is set.

### Integration test (`tests/integration/test_talk_dispatch_e2e.py` — new file)

End-to-end with the fake-Claude binary fixture: start a talk, fake agent calls `opc dispatch`, verify a new task gets enqueued and runs to terminal under the assigned agent. Marked `@pytest.mark.integration` so it's excluded from default runs (per CLAUDE.md test conventions).

## Architectural notes

### `run_step` already supports worker-as-root-assignee

`src/orchestrator/run_step.py:92` reads `task.assigned_agent` and branches on `orch.teams.is_team_manager(agent)` (line 122). A root task with `assigned_agent=<worker>` takes the worker branch, runs the worker session, and is classified at completion the same as any other worker session. No orchestrator change required for the dispatch path itself.

### Performance scoring bypasses worker self-dispatched tasks

`_log_verdict_if_delegated` (`run_step.py:419`) only emits a `review_verdict` audit row when the worker has a parent (i.e., the task was delegated by a manager). A root task with `assigned_agent=<worker>` has `parent_task_id=NULL`, so no verdict is logged, and the worker's 30-day rolling scorecard does not move because of self-dispatched work.

This is the correct behavior given the authority model — there's no manager to review the work, and the founder co-presence in the talk *is* the gate. But it means a worker can avoid scorecard pressure by routing requests through talks. The mitigation is operational, not technical: `task_dispatched` audit entries are queryable and `opc audit --action task_dispatched --agent <worker>` surfaces the volume. If abuse becomes a real concern, a future spec can add either a synthetic verdict (founder co-presence stamps an approve) or a separate dispatched-task scorecard.

### Open question for plan stage: `team_for_agent` lookup

`TeamRegistry` today exposes `team_for_manager(name)` and `is_team_manager(name)` but no `team_for_agent(name)` for workers. The plan needs to add it. Workers' team membership is implicit in the team rosters that the registry already loads (`src/orchestrator/teams.py` — to be confirmed during planning). The plan should pick the cheapest synchronous path: a reverse-index dict built at registry load time is the obvious shape. If the registry doesn't carry worker membership at all (dynamically-enrolled workers like `senior_dev` aren't in the static config), the helper falls back to `agent_enrollments.team` — which the plan should add as a column if it isn't there.

## Out of scope

- **Cross-team dispatch.** Any cross-team need still flows through `opc run` (founder) or escalation rules.
- **Task-path auth on dispatch.** In-task agents already have `decision: delegate` (managers) or normal task work (workers). No demand for a "dispatch from inside a task" path; adding it would duplicate existing flows.
- **Multi-task atomic dispatch.** One call, one task. If the founder wants three tasks dispatched, the agent makes three calls.
- **Talk-side audit entries.** `audit_log` is task-scoped today; not changing that for this feature. The new task's row carries the talk_id in its payload for traceability.
- **Dispatch quotas per talk.** No hard cap on how many tasks one talk can dispatch. If abuse becomes a real concern, revisit.
- **Founder-issued dispatch from a talk.** This spec is about **agents** dispatching while in talks. The founder, when in a talk, doesn't need a special path — they can always just run `opc run` in another shell. (The talk session is the agent's session; the founder's CLI is unaffected.)

## References

- `docs/superpowers/specs/2026-04-17-manage-agent-design.md` — the dual-auth precedent (task-path + talk-path) we're modeling on.
- `docs/superpowers/specs/2026-04-21-talk-flow-design.md` — talk lifecycle, transcript model, abandon/end semantics.
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — column + audit-entry pattern for "task spawned from non-task context."
- `protocol/skills/talk/SKILL.md` — existing carve-out for `manage-agent` during talks; the model for the new dispatch carve-out.
