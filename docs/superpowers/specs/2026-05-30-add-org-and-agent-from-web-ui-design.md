# Add Org and Add Agent from the Web UI

**Status:** design
**Date:** 2026-05-30

## Problem

Today the web app can list orgs and agents but cannot create them. New orgs
require running `grassland orgs init <slug>` from a terminal, and new agents
require either hand-editing `<runtime>/orgs/<slug>/org/agents/<name>.md`
(managers) or going through the manager-driven talk + `manage-agent enroll`
flow (workers). The founder cannot stay in the browser to grow an org from
empty to fully populated.

This spec adds two founder-driven creation flows to the web app:

1. **Add org** — slug-only, empty org. No sample seeding from the UI.
2. **Add agent** — supports both "add worker to existing team" and "add
   manager + create new team in one step". Lands the agent active
   immediately; no pending → approve hop.

It also adds the minimal teams-read API the agent dialog needs.

## Non-goals

- Editing or terminating agents from the web. Termination still flows
  through manager talk + `manage-agent` (founder UI for cross-team founder
  authority is a separate, larger discussion).
- Editing teams (renaming, swapping manager, removing members).
- Deleting orgs from the UI. `DELETE /orgs/{slug}` exists but has a no-
  active-tasks gate; UI for that is a separate flow.
- Seeding orgs from `examples/orgs/<name>` samples. CLI-only for now.
- Changes to the existing pending-approve flow. It stays as-is for the
  manager-driven enrollment path.

## Architecture

Three layers:

- **UI dialogs** in the web app, one for each flow.
- **New founder-facing daemon routes** added under the existing per-org
  router (`/api/v1/orgs/{slug}/...`):
  - `GET /teams` — list teams + their managers (drives the dropdown)
  - `POST /agents` — founder-driven enroll that lands active immediately
- **One new method on `TeamsRegistry`** — `add_team(name, manager)`.

Existing routes used as-is:

- `POST /api/v1/orgs` (already exists, already mirrored as `createOrg`)
- All read paths under `/api/v1/orgs/{slug}/agents` (unchanged)

### Why a new `POST /agents` instead of extending `POST /agents/manage`

`POST /agents/manage` is hard-bound to team-manager auth (`talk_id` or
`task_id + session_id`) and enforces same-team-only enrollment. Founder
context is fundamentally different — bearer-token only, no session — and
the founder can target any team. Forcing a "founder branch" through
`manage-agent` would tangle two unrelated auth models in one route. A new
endpoint keeps the existing manager-driven path untouched and the founder
path narrow.

### Why land active immediately, not pending

The pending → approve flow exists so a manager can propose a worker and a
founder can gate it. When the founder is the proposer, there is no second
authority — having to click Approve on something they just typed is
busywork. The new endpoint does internally what `approve_agent` does
today: write the file to the active `agents/` directory, register the
team change, and run `ensure_workspace_ready` to clone repos and
materialize `CLAUDE.md` / `AGENTS.md` for the chosen executor.

## Backend changes

### `TeamsRegistry.add_team(name, manager)` (new)

`src/orchestrator/teams.py`. Inserts a new team with the given manager and
empty workers. Raises `ValueError` if `name` already exists. Auto-persists
to `teams.yaml` when `self._root is not None`, matching the existing
`add_worker` / `remove_worker` semantics.

### `GET /api/v1/orgs/{slug}/teams` (new)

`src/daemon/routes/teams.py` (new file). Returns:

```json
{
  "teams": [
    { "name": "engineering", "manager": "engineering_head",
      "workers": ["product_manager", "dev_agent"] }
  ]
}
```

Sorted by team name. Reads from `org.teams` (the in-memory
`TeamsRegistry`). If `org.teams is None` (no active runtime), returns
`{"teams": []}` — same shape as an empty registry.

### `POST /api/v1/orgs/{slug}/agents` (new — founder-create)

Lives in `src/daemon/routes/agents.py`. Bearer auth only (no session).

Request body:

```json
{
  "name": "delta_worker_1",
  "role": "worker" | "manager",
  "team": "alpha",
  "new_team": "delta",
  "executor": "claude" | "codex" | "opencode",
  "description": "...",
  "system_prompt": "...",
  "allow_rules": ["gh pr comment"],
  "repos": { "name": "url" }
}
```

Validation (all 4xx are returned as `HTTPException` with a `code` in
`detail`):

| Check | Status | `code` |
|---|---|---|
| `name` matches `^[a-z][a-z0-9_]*$` | 422 | `invalid_agent_name` |
| `name` already in pending or active | 409 | `agent_exists` |
| `role=worker` requires `team` set, `new_team` absent | 422 | `role_team_mismatch` |
| `role=manager` requires `new_team` set, `team` absent | 422 | `role_team_mismatch` |
| `role=worker` and `team` not in registry | 404 | `unknown_team` |
| `role=manager` and `new_team` already in registry | 409 | `team_exists` |
| `description` or `system_prompt` empty | 422 | `missing_required_field` |
| `executor` not in {claude, codex, opencode} | 422 | (pydantic Literal) |
| `allow_rules` contain unsafe chars (`\n \r ; \| & ` `` ` `` `$(`) | 422 | (extract `_validate_allow_rules` from the existing `ManageAgentBody._reject_unsafe_allow_rules` into a module-level function in `agents.py`, call from both bodies) |

Behavior on success (in this order, under `org.teams_lock`):

1. If `role=manager`: `org.teams.add_team(new_team, manager=name)`.
2. If `role=worker`: `org.teams.add_worker(team, name)`.
3. Build `AgentDef` with `enrolled_by="founder"`, `enrolled_at=now`,
   `enrolled_at_task=None`.
4. Write directly to `<paths.agents_dir>/<name>.md` (skip `_pending/`),
   via the same atomic tempfile + `os.replace` pattern used by
   `manage_agent.update`.
5. Bootstrap workspace, same sequence as `approve_agent`:
   - `workspace.mkdir(parents=True, exist_ok=True)`
   - `write_default_agent_config(workspace)`
   - `set_executor(workspace, agent_def.executor)`
   - For each repo in `agent_def.repos`: `add_repo` + `ctx.clone_repo`
   - `ctx.ensure_workspace_ready(workspace, name, system_prompt, provider=executor)`
   - `ctx.create_agent_dirs(workspace, name)`
6. `AuditLogger(org.db).log_agent_managed(scope_id="founder", action="enroll", name=<name>, source="founder", actor="founder")`.

Returns `{"name": <name>, "team": <team>|<new_team>, "role": <role>}`.

Failure recovery: if step 5 (workspace bootstrap) fails partway, the
agent file and teams.yaml row are kept and the founder can retry via
`grassland init-agent <name>`. This matches `approve_agent`'s current
behavior — adding a rollback only here would diverge the two paths. The
audit row is logged in step 6 only if step 5 completes; mid-bootstrap
failures bubble as a 500 and the founder retries.

### Audit `scope_id="founder"` rationale

`log_agent_managed` accepts a `scope_id` string; existing manager-driven
calls pass the `task_id` or `talk_id`. The founder path has no task or
talk, so we record the literal string `"founder"`. This matches the
"actor / source / scope" trio that the audit viewer already filters on.

## Frontend changes

### Files

| Path | Change |
|---|---|
| `web/src/lib/api/teams.ts` | new — `listTeams(slug)` |
| `web/src/lib/api/agents.ts` | add `createAgent(slug, body)` |
| `web/src/lib/api/index.ts` | re-export `teams` |
| `web/src/test/openapi-coverage.test.ts` | add the two new paths to `INCLUDED_PATHS` |
| `web/src/hooks/agents.ts` | add `useCreateAgent` |
| `web/src/hooks/teams.ts` | new — `useTeamsList` |
| `web/src/design-system/providers/DataContext.ts` | extend with `teams` + `agents.useCreateAgent` |
| `web/src/design-system/providers/{_real,_mock}-teams.ts` | new |
| `web/src/design-system/providers/{_real,_mock}-agents.ts` | add create-mutation |
| `web/src/design-system/layouts/AppShell/TopBar.tsx` | "+ Add org" affordance + dialog mount |
| `web/src/features/orgs/AddOrgDialog.tsx` | new |
| `web/src/features/agents/AddAgentDialog.tsx` | new |
| `web/src/features/agents/AgentsPage.tsx` | "Add agent" button + empty-state CTA |

### Contract pinning

Regenerate `tests/contract/openapi.json` with
`GRASSLAND_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
Add to `INCLUDED_PATHS`:

- `GET /api/v1/orgs/{slug}/teams`
- `POST /api/v1/orgs/{slug}/agents`

### Add Org dialog

Mount point: `TopBar`. The existing org `<Select>` gets an "+ Add org…"
trailing item; clicking it opens the dialog. (Falling back to a sibling
`+` button if the Select primitive can't render a non-item entry
cleanly — decided during implementation.)

Form:

- **Slug** input. Client-side validator: matches the same
  `^[a-z0-9-]{1,40}$` the server enforces. Inline error below the field.
- Buttons: Cancel, Create. Submit disabled until the slug passes the
  regex.

On success:

- Invalidate the `['orgs']` query.
- Navigate to `/orgs/<new_slug>/threads`.

Errors from the server (409 `org_exists`, 400 `invalid_slug`) surface
inline in the dialog.

### Add Agent dialog

Mount point: `AgentsPage` header — new "Add agent" button to the right of
the existing Tabs. Also wired from the empty-state CTA on the Active tab
when `agents.length === 0`.

Form:

```
┌─ New agent ───────────────────────┐
│ Name:  [_______________]          │
│ Role:  ( ) Worker  ( ) Manager    │
│                                   │
│ — If Worker —                     │
│ Team:  [Select team ▾]            │
│                                   │
│ — If Manager —                    │
│ New team name: [____________]     │
│                                   │
│ Executor: [claude ▾]              │
│ Description: [____________]       │
│ System prompt: [textarea]         │
│ ▸ Advanced                        │
│   Allow rules: [chips input]      │
│   Repos: [name + url pairs]       │
│                                   │
│              [Cancel]  [Create]   │
└───────────────────────────────────┘
```

**New team name defaulting (manager role):** when the role is `manager`
and the user has not manually edited the new team field, the value
auto-tracks the agent name with the trailing `_<suffix>` stripped:

| Agent name | Default new_team |
|---|---|
| `engineering_head` | `engineering` |
| `content_manager` | `content` |
| `tourism_ops` | `tourism` |
| `singleword` | `singleword` (no underscore — leave as-is) |

Implementation: a `linkedToName` boolean state. Initialized `true`.
Typing in the agent name updates `new_team` only while `linkedToName`.
The first user edit of the new team field flips `linkedToName` to
`false` (standard "linked-until-touched" pattern). Switching role away
from `manager` and back resets the link.

**Team dropdown (worker role):** populated by `useTeamsList()`. If
`teams.length === 0`, the dropdown is replaced by an inline note —
"No teams yet. Add a manager to create the first team." — and the
Create button stays disabled while role=worker.

**Validation (client-side):**

- Name: same regex as server.
- Required: name, executor, description, system_prompt, team or new_team
  depending on role.
- Allow rules: each chip rejected client-side if it contains the unsafe
  chars list (mirror of server validator).

**Submission:** on success — invalidate `['agents', slug]` and
`['enrollments', slug]` queries, close the dialog. New agent appears on
the Active tab.

Server errors surface inline; the form does NOT reset on error so the
founder can correct and resubmit.

## Testing

### Backend unit tests

- `tests/unit/test_teams_registry.py` — `add_team` happy path; raises on
  duplicate; auto-persists when `_root` set; does not auto-persist when
  `_root` is `None`.
- `tests/unit/test_routes_agents_founder_create.py` — one test per
  validation row in the table above; one happy-path worker-into-team;
  one happy-path manager-with-new-team; concurrency: two simultaneous
  creates of the same name → one wins, the other returns 409.
- `tests/unit/test_routes_teams.py` — `GET /teams` returns sorted
  shape; empty registry returns empty list; no-runtime returns empty
  list.

### Backend integration test

`tests/integration/test_founder_creates_org_and_agents_e2e.py` — one
test:

1. `POST /api/v1/orgs` with a fresh slug → 200.
2. `POST /agents` with `role=manager, new_team=alpha, name=alpha_head`
   → 200.
3. `POST /agents` with `role=worker, team=alpha, name=alpha_worker_1`
   → 200.
4. Read `<slug>/org/teams.yaml` directly — assert shape:
   `{teams: {alpha: {manager: alpha_head, workers: [alpha_worker_1]}}}`.
5. Assert `<slug>/org/agents/alpha_head.md` and
   `<slug>/org/agents/alpha_worker_1.md` exist (active, not pending).
6. Assert `<slug>/workspaces/alpha_head/CLAUDE.md` exists
   (workspace bootstrap ran).
7. Assert two `agent_managed` audit rows exist with
   `actor="founder"`, `source="founder"`.

### Web tests

- `web/src/features/orgs/AddOrgDialog.test.tsx` — slug validation
  (valid / invalid / too long); Create disabled until valid; 409 from
  server renders inline.
- `web/src/features/agents/AddAgentDialog.test.tsx` — role toggle
  swaps the team field; new_team defaulting tracks until manually
  edited; submit body matches expected shape for both branches; empty
  teams list disables worker create.
- `web/src/test/openapi-coverage.test.ts` — stays green after the
  two `INCLUDED_PATHS` additions.

## Load-bearing invariants

The following invariants are easy to break and worth documenting in
CLAUDE.md after merge:

- **Founder-create skips `_pending/` entirely.** Do not "reuse approve"
  by writing pending then calling `approve_agent` internally; that path
  is two file moves under one lock and is harder to reason about than a
  direct write. The two paths are independently audited (source=founder
  vs source=task/talk).
- **`teams.add_team` precedes the agent file write under one lock, with
  rollback on write failure.** If the agent file write raises,
  `remove_worker` (worker case) or `remove_team` (manager case) is
  called to undo the registry mutation, so the founder can retry the
  POST without hitting a phantom 409 `team_exists`. The original spec
  considered registry-first irreversible by design; the actual route is
  registry-first + explicit rollback so retries always make progress.
- **`enrolled_by="founder"`** is the only marker distinguishing founder-
  created from manager-created agents in the rendered `.md` frontmatter.
  Future tooling that filters by enrollment source must rely on this
  literal string.
