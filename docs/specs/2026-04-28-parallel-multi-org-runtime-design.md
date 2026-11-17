# Parallel Multi-Org Runtime Design

**Status:** Draft
**Author:** Founder + Claude
**Date:** 2026-04-28
**Supersedes:** [2026-04-26-multi-org-runtime-design.md](2026-04-26-multi-org-runtime-design.md)

## 1. Problem

Today, `DaemonState` holds a **single** runtime, DB, queue, event_bus,
sessions, teams registry, and Orchestrator. Every HTTP route reads
`request.app.state.daemon` directly with no slug parameter. `next_task_id`
mints `TASK-NNN` from the per-runtime DB, so two runtimes would collide.
`/runtimes/activate` is implemented as swap-and-tear-down and 409s if any
task is in flight. The result: registered orgs are serial, not parallel.

The 2026-04-26 spec proposed making the daemon shard `DaemonState` by
registry slug, with one filesystem runtime per org. That model preserves
the "runtime == org" identity but multiplies registered runtimes and ops
surface (one `~/.opc/runtimes.yaml` entry per org, slug stamped on every
runtime's `opc.yaml`).

A simpler model is available: **one runtime container, N org subfolders
inside it, all running concurrently in one daemon process.** Orgs are
tenants of a runtime, not separate runtimes. Cross-org isolation stays
structural (per-org DB, workspaces, KB, talks, agents) but operationally
there's still one daemon, one auth token, one process to start/stop. The
"multi-runtime" concept collapses — there's just one runtime, and orgs
live inside it.

## 2. Goals & non-goals

### Goals

1. One daemon process serves N orgs running tasks **concurrently**.
2. Filesystem layout: one runtime container; orgs are subdirectories under
   `<runtime>/orgs/<slug>/`.
3. Each org owns its own SQLite DB, workspaces, KB, talks, and agents
   directory. Cross-org leakage is structurally impossible.
4. Every per-org HTTP route is path-prefixed `/api/v1/orgs/<slug>/...`.
   Cross-org routes (list orgs, create org) live above that.
5. Every per-org CLI command takes `--org <slug>`. No `active-org` file,
   no `opc use` command. Single-org-after-migration ergonomics fall out
   of (a) explicit `--org`, (b) optional `OPC_ORG_SLUG` env var, or
   (c) auto-infer when exactly one org exists.
6. Worker pool is **global**: one queue holding `(org_slug, task_id)`
   tuples; N workers pop and dispatch to the right org's orchestrator.
   Bounded total concurrency on the host.
7. Migration from today's single-org-per-runtime layout is a one-shot,
   founder-run, TTY-gated script. Hard cut, no v1 backward compat.

### Non-goals (carried forward from 2026-04-26 spec)

- Per-org auth tokens. Single shared bearer token at `~/.opc/daemon.token`.
- Per-org `Settings` (env-var-driven config knobs).
- Cross-org KB sharing. KB is per-org, full stop.
- Per-org rate limiting / per-org worker quotas.
- Concurrent-multi-tenancy auth boundaries (per-org RBAC).
- Hard delete of an org folder via CLI. Org removal is `rm -rf` by hand.
- Multi-container runtime deployments (e.g., dev + prod containers in one
  `runtimes.yaml`). The registry stays single-active; multiple registered
  containers remain technically supported (existing behavior) but no new
  workflow optimizes for it.

## 3. Architecture summary

```
~/.opc/                              # daemon home, unchanged
  daemon.{pid,port,token,log}
  runtimes.yaml                      # one active container path

<runtime>/                           # multi-org container
  opc.yaml                           # { schema_version: 2, type: multi-org-runtime }
  orgs/
    <slug-A>/                        # one org (slug == directory name)
      org/                           # charter, escalation-rules, teams.yaml, agents/
      workspaces/<agent>/            # this org's agents only
      kb/                            # this org's KB
      talks/                         # this org's talks
      opc.db                         # this org's SQLite
    <slug-B>/
      ...
```

The daemon holds a `dict[slug, OrgState]`. Each `OrgState` owns its DB,
event bus, session tracker, teams registry, and asyncio locks. One global
`TaskQueue` carries `(slug, task_id)` tuples; N workers pop and dispatch
to `state.orgs[slug].orchestrator.run_step(task_id)`.

## 4. Filesystem layout

### 4.1 Runtime container marker

`<runtime>/opc.yaml`:

```yaml
schema_version: 2
type: multi-org-runtime
created_at: 2026-04-28T10:00:00Z
```

No slug at the runtime level. The runtime is a container, not an identity.
`schema_version: 2` distinguishes it from pre-migration single-org runtimes
(which had `schema_version: 1` and a `slug:` field).

### 4.2 Org directory

`<runtime>/orgs/<slug>/` — slug is the directory name. No per-org marker
file; the directory's existence + presence of `org/teams.yaml` is the
identity.

Slug rules: `^[a-z0-9-]{1,40}$` (matches today's slug rules). Reserved
names: `_pending`, `_archive`. Daemon refuses to load orgs with reserved
or invalid slugs and logs a warning.

### 4.3 Subfolder semantics (unchanged from 2026-04-26 spec §5)

Inside each `<runtime>/orgs/<slug>/`:

- `org/charter.md`, `org/escalation-rules.md` — reference docs, humans edit.
- `org/teams.yaml` — team layout; read by `TeamsRegistry`.
- `org/agents/<name>.md`, `org/agents/_pending/<name>.md` — agent
  definitions, file format from 2026-04-26 §5.2.
- `org/config.yaml` — optional per-org config (e.g.,
  `session_timeout_seconds`).
- `workspaces/<agent>/` — per-agent workspaces; `CLAUDE.md` or `AGENTS.md`,
  `.claude/skills/` or `.agents/skills/`, `repos/`, `agent.yaml`,
  `learnings.md`, `task_history.md`.
- `kb/` — per-org knowledge base.
- `talks/` — per-org talk transcripts.
- `opc.db` — per-org SQLite (audit log, scorecards, tasks, talks, kb meta).

## 5. Daemon state

### 5.1 `OrgState`

```python
@dataclass
class OrgState:
    slug: str
    root: Path                              # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    sessions: SessionTracker
    event_bus: EventBus
    db_lock: asyncio.Lock
    kb_lock: asyncio.Lock
    teams_lock: asyncio.Lock
    orchestrator: Orchestrator              # bound to this OrgState's resources

    @classmethod
    def load(cls, root: Path, settings: Settings) -> "OrgState": ...
    def close(self) -> None: ...            # close DB, drop in-memory state
```

`OrgState.load` reads `<root>/org/teams.yaml`, opens `<root>/opc.db`,
constructs an `Orchestrator` wired to this org's resources. Each `OrgState`
is fully self-contained — no cross-references to other orgs.

### 5.2 `DaemonState`

```python
@dataclass
class DaemonState:
    runtime: RuntimeDir | None              # the multi-org container
    settings: Settings
    orgs: dict[str, OrgState] = {}          # keyed by slug
    queue: TaskQueue                        # GLOBAL, items are (slug, task_id)
    orgs_lock: asyncio.Lock                 # guards orgs-dict mutations

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState": ...
    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        """Load all orgs under <runtime>/orgs/* on startup."""
        ...

    def get_org(self, slug: str) -> OrgState:
        """Return OrgState or raise HTTPException 404."""
        ...

    async def add_org(self, slug: str) -> OrgState:
        """Lazy-load an org's OrgState; idempotent."""
        ...
```

`DaemonState` is a thin registry over `OrgState`s. The settings, queue,
and runtime container are process-global; everything else is per-org.

### 5.3 Lifespan

- **Startup:** if a runtime is registered, scan `<runtime>/orgs/` and load
  one `OrgState` per non-`_pending` subdirectory. Start N global workers.
- **Idle boot:** if no runtime is registered, stay idle. Routes 409 with
  `no_active_runtime` until `opc init <path>` runs.
- **Add org at runtime:** `opc orgs init <slug>` route creates the
  subfolder skeleton and calls `state.add_org(slug)` — no daemon restart.
- **Shutdown:** drain queue, then call `OrgState.close()` on every org in
  parallel; bounded shutdown timeout shared across orgs.

### 5.4 Worker pool

```python
class TaskQueue:
    _queue: asyncio.Queue[tuple[str, str]]    # (slug, task_id)

    def enqueue(self, slug: str, task_id: str) -> None: ...
```

Workers pop a `(slug, task_id)` tuple, look up `state.get_org(slug)`, and
call `org.orchestrator.run_step(task_id)`. Default worker count is N=3
(today's value). Total in-flight tasks across all orgs is capped at N.

Per-org fairness is best-effort FIFO. If one org floods the queue, others
wait. If that becomes a real problem, a per-org soft cap can be added
later as a small dispatcher change; the data model doesn't change.

## 6. HTTP routing

### 6.1 Route topology

```
GET    /api/v1/health
GET    /api/v1/runtime                    # info about the container
POST   /api/v1/runtime                    # create + register the container
POST   /api/v1/runtime/use                # swap active container (rare)
# Removed: POST /runtimes/register, POST /runtimes/activate, GET /runtimes
# (the multi-runtime registry surface is gone — only one container is active,
#  and it's manipulated via /runtime singular)

GET    /api/v1/orgs                       # list orgs in the active runtime
POST   /api/v1/orgs                       # create a new org (init skeleton)
DELETE /api/v1/orgs/{slug}                # unload from memory; folder NOT deleted

# Per-org routes — all gated by require_org dependency
POST   /api/v1/orgs/{slug}/tasks
GET    /api/v1/orgs/{slug}/tasks
GET    /api/v1/orgs/{slug}/tasks/{task_id}
GET    /api/v1/orgs/{slug}/tasks/{task_id}/events           (SSE)
POST   /api/v1/orgs/{slug}/tasks/{task_id}/completion
POST   /api/v1/orgs/{slug}/tasks/{task_id}/cancel
POST   /api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation
POST   /api/v1/orgs/{slug}/tasks/{task_id}/revisit
GET    /api/v1/orgs/{slug}/tasks/{task_id}/recall
GET    /api/v1/orgs/{slug}/agents
POST   /api/v1/orgs/{slug}/agents/init                       (SSE)
POST   /api/v1/orgs/{slug}/agents/manage
POST   /api/v1/orgs/{slug}/agents/{name}/learnings
POST   /api/v1/orgs/{slug}/agents/{name}/repos
GET    /api/v1/orgs/{slug}/agents/enrollments
POST   /api/v1/orgs/{slug}/agents/{name}/approve
POST   /api/v1/orgs/{slug}/agents/{name}/reject
GET    /api/v1/orgs/{slug}/audit
GET    /api/v1/orgs/{slug}/kb
GET    /api/v1/orgs/{slug}/kb/{kb_slug}
GET    /api/v1/orgs/{slug}/kb/search
POST   /api/v1/orgs/{slug}/kb
POST   /api/v1/orgs/{slug}/kb/{kb_slug}
POST   /api/v1/orgs/{slug}/kb/reindex
POST   /api/v1/orgs/{slug}/kb/precedent
DELETE /api/v1/orgs/{slug}/kb/{kb_slug}
POST   /api/v1/orgs/{slug}/talks
GET    /api/v1/orgs/{slug}/talks
GET    /api/v1/orgs/{slug}/talks/{talk_id}
POST   /api/v1/orgs/{slug}/talks/{talk_id}/end
POST   /api/v1/orgs/{slug}/talks/{talk_id}/abandon
POST   /api/v1/orgs/{slug}/talks/{talk_id}/resume
POST   /api/v1/orgs/{slug}/talks/{talk_id}/dispatch
```

### 6.2 `OrgDep` dependency

```python
def resolve_org(slug: str, request: Request) -> OrgState:
    state: DaemonState = request.app.state.daemon
    if state.runtime is None:
        raise HTTPException(409, detail={"code": "no_active_runtime"})
    try:
        return state.get_org(slug)
    except KeyError:
        raise HTTPException(
            404,
            detail={
                "code": "unknown_org",
                "slug": slug,
                "available": sorted(state.orgs.keys()),
            },
        )

OrgDep = Annotated[OrgState, Depends(resolve_org)]
```

Every per-org route signature receives `org: OrgDep` and reads `org.db`,
`org.teams`, `org.event_bus`, etc. — never `request.app.state.daemon`
directly. The token check (`require_token()`) runs before slug resolution
so unauthed requests never touch the registry.

### 6.3 Auth scope

Single bearer token (`~/.opc/daemon.token`). Token grants access to all
orgs. Per-org auth is out of scope (see §2).

## 7. CLI surface

### 7.1 Container commands (no `--org`)

| Command | Purpose |
|---|---|
| `opc init <path>` | Create + register a multi-org container (`opc.yaml v2`) |
| `opc runtime` | Print info about the active container |
| `opc use <path>` | Swap the active container (rare) |

### 7.2 Org-management commands (no `--org`)

| Command | Purpose |
|---|---|
| `opc orgs` | List orgs in the active container |
| `opc orgs init <slug> [--from <example-path>]` | Create an org subfolder; optionally seed from `examples/orgs/...` |
| `opc orgs unload <slug>` | Drop OrgState from memory; folder unchanged |

(`opc orgs unload` is rarely useful; primarily a debug knob. There's no
"delete org" command; `rm -rf` is the answer.)

### 7.3 Per-org commands (require `--org`)

```
opc run --org <slug> --brief "..." [--team <team>]
opc tasks --org <slug>
opc tail --org <slug> TASK-001
opc details --org <slug> TASK-001
opc audit --org <slug> [TASK-001] [--agent X] [--limit N]
opc agents --org <slug> [--detail]
opc init-agent --org <slug> [<name>]
opc enrollments --org <slug> [--status pending]
opc approve-agent --org <slug> <name>
opc reject-agent --org <slug> <name>
opc resolve-escalation --org <slug> --task-id ... --decision ... --rationale "..."
opc revisit --org <slug> TASK-052 [--note "..."]
opc kb {list|get|add|update|delete|reindex|precedent|search} --org <slug> ...
opc talk {start|end|abandon|resume|status|list|show} --org <slug> ...
opc recall --org <slug> TASK-001 [--tree] [--fetch-artifact <relpath>]
```

### 7.4 `--org` resolution

Resolution order on every per-org command:

1. Explicit `--org <slug>` flag — wins.
2. `OPC_ORG_SLUG` environment variable.
3. If exactly one org exists in the active container, infer it.
4. If multiple orgs exist, hard error:
   ```
   error: --org <slug> is required
   available orgs:
     hk-tourism
     lisbon-tourism
   ```
5. If zero orgs exist (just-initialized container), hard error:
   ```
   error: no orgs registered yet
   create one with: opc orgs init <slug> [--from <example-path>]
   ```

Step 3 covers the post-migration single-org case so existing scripts keep
working without modification.

### 7.5 Agent-side callbacks

Every callback the start-task / manage-* / talk skills emit gains
`--org <slug>`. The slug is **injected at workspace-build time** as a
literal in the skill files copied into the agent's workspace. The agent
never reads `OPC_ORG_SLUG` itself — the value is baked into its skill
templates so misconfiguration can't cross-route a callback.

```
opc report-completion --org <slug> --task-id ... --session-id ... ...
opc learning --org <slug> --agent <you> --session-id ... --task-id ... --text ...
opc manage-repo {add|remove|update} --org <slug> --agent <you> ...
opc manage-agent --org <slug> --from-file ...
opc kb add --org <slug> --agent <you> --from-file ...
opc kb update --org <slug> --agent <you> --from-file ...
opc kb delete --org <slug> --agent <you> --confirm [--as-founder]
opc kb precedent --org <slug> --task-id ... --decision ... --rationale "..." --as-founder
opc dispatch --org <slug> --from-file ...
```

### 7.6 Skill template substitution

Today skills are copied verbatim from `protocol/skills/` into
`<workspace>/.claude/skills/` (Claude) or `.agents/skills/` (Codex). After
the change, the copy substitutes `{ORG_SLUG}` placeholders with the
literal slug. Skills with no placeholder copy unchanged.

`opc init-agent --org X` re-runs the copy on every invocation, so updating
a skill source in `protocol/` and re-running `init-agent` is the supported
roll-out path.

## 8. Task and talk IDs

Per-org sequential — `TASK-001` is local to one org's DB. The slug is
always in the URL path, so `(slug, task_id)` is the global key. CLI
displays bare IDs in single-org context and `<slug>:TASK-001` in
cross-org contexts (none today; reserved for future cross-org tooling).

`SessionTracker` keys remain `(task_id, agent)` — but the tracker lives
inside `OrgState`, so two orgs both having `(TASK-001, dev_agent)` is
fine: they're in different tracker instances. Same logic for `EventBus`.

## 9. Migration

### 9.1 One-shot script

```
uv run opc migrate-to-multi-org <runtime-path> --i-have-a-backup [--apply]
```

`--i-have-a-backup` is mandatory. Without `--apply` the script is dry-run
and prints all intended actions. Without `--i-have-a-backup` the script
aborts before doing anything. TTY-gated: refuses to run without an
attached terminal.

### 9.2 Steps

1. **Validate prerequisites.** Path is a valid runtime with `opc.yaml`.
   Read `slug` and `schema_version`.
2. **Detect already-migrated.** If `opc.yaml.schema_version >= 2` OR
   `<runtime>/orgs/` already exists with content, report "already
   migrated" and exit 0.
3. **Refuse if any tasks or talks are in flight.** Scan the legacy `opc.db`
   for non-terminal tasks; if any exist, abort with
   `cannot_migrate_with_active_tasks` and list the task ids. Same for open
   talks (`talks.status = 'open'`) — abort with `cannot_migrate_with_open_talks`
   and list talk ids. Founder must end/abandon talks and resolve tasks
   first.
4. **Create `<runtime>/orgs/<old-slug>/`.** `os.makedirs` with
   `exist_ok=False` (fail loudly if the directory exists).
5. **Move the four data subfolders + DB.** `os.rename` (atomic on POSIX
   within the same filesystem):
   - `<runtime>/org` → `<runtime>/orgs/<old-slug>/org`
   - `<runtime>/workspaces` → `<runtime>/orgs/<old-slug>/workspaces`
   - `<runtime>/kb` → `<runtime>/orgs/<old-slug>/kb`
   - `<runtime>/talks` → `<runtime>/orgs/<old-slug>/talks`
   - `<runtime>/opc.db` → `<runtime>/orgs/<old-slug>/opc.db`
6. **Rewrite top-level `opc.yaml`.** Drop `slug`. Set
   `schema_version: 2`, `type: multi-org-runtime`. Preserve `created_at`.
7. **Print summary.** New layout, slug of the migrated org, command to
   start the daemon.

### 9.3 Daemon refuses v1 layout post-migration code

The daemon's `RuntimeDir.load` checks `schema_version`. If `< 2`, it
refuses to boot with a clear message:

```
runtime at <path> is schema_version 1 (single-org).
run `opc migrate-to-multi-org <path> --i-have-a-backup --apply` to migrate.
```

No silent fallback. The post-migration code path doesn't carry v1 logic.

### 9.4 Workspace impact

After migration, `<runtime>/orgs/<old-slug>/workspaces/<agent>/` still
contains the agent's `CLAUDE.md` / `AGENTS.md`, skills, and `agent.yaml`
— but those skills' `opc` callback templates reference no slug. The
founder must re-run `opc init-agent --org <old-slug>` (no name; rebuilds
all) to regenerate workspaces with `--org <slug>` baked into every skill
callback. Migration prints this as the final step:

```
next step:
  uv run opc init-agent --org <old-slug>
```

### 9.5 Rollback

None. Hard cut. `git revert` reverses code changes; the runtime folder
restoration is on the founder (`--i-have-a-backup` flag is the explicit
ack). The migration script is single-direction by design — supporting
"unmigrate" doubles the surface area for negligible benefit.

## 10. Testing approach

### 10.1 Unit tests

| Area | Coverage |
|---|---|
| `RuntimeDir` | `schema_version: 2` marker round-trip; refuses v1 with clear error; `orgs_dir` property; iteration over `orgs/*` |
| `OrgState.load` | DB open, teams load, orchestrator wiring; isolation between two `OrgState`s constructed against tmp_path orgs |
| `OrgState.close` | DB closes, queue drained, sessions cleared |
| `DaemonState.from_runtime` | Discovers all orgs under `<runtime>/orgs/`; skips reserved names (`_pending`, `_archive`); skips dirs missing `org/teams.yaml` |
| `DaemonState.add_org` | Idempotent; concurrent `add_org` calls don't double-load |
| `resolve_org` | Returns right OrgState; 404 with available list on unknown slug |
| Worker queue | `(slug, task_id)` tuple round-trip; worker dispatches to right OrgState |
| CLI `--org` resolution | flag > env > auto-infer (single-org) > error (multi-org) |
| `opc orgs init` | Creates skeleton; idempotent if already exists |
| Migration | Dry-run vs apply parity; idempotent on already-migrated; aborts without `--i-have-a-backup`; aborts with active tasks; preserves `created_at` |
| Routes | Every per-org route honors `OrgDep`; cross-org write attempts via wrong slug 404 |
| Skill template substitution | `{ORG_SLUG}` replaced; skills without placeholder unchanged |

### 10.2 Integration tests (`pytest -m integration`)

| Scenario | Coverage |
|---|---|
| **Two orgs concurrent** | One daemon, two orgs initialized, one task submitted to each within 100ms — both run to completion in parallel using fake Claude binaries; assert no cross-org audit log entries |
| **Cross-org isolation** | Org A's task creates KB entry; assert Org B's `/kb` does not list it. Org A's session tracker has `TASK-001`; Org B independently has its own `TASK-001` with no collision |
| **Add org while daemon is running** | Submit a task to Org A. While it runs, `opc orgs init org-b` and submit to Org B. Both complete |
| **Migration end-to-end** | Stand up a v1-shape runtime fixture (today's layout with one slug + one DB), run migration, then daemon boots and serves tasks to the migrated org |
| **`--org` resolution** | After migration, `OPC_ORG_SLUG=<slug> opc tasks` works; `opc tasks` without env or flag auto-infers the single org; after `opc orgs init second-org`, `opc tasks` errors with the available list |
| **Bootstrap from example** | `opc init <tmp>` → `opc orgs init hk --from examples/orgs/hk-macau-tourism` → `opc init-agent --org hk` (no name) → `opc run --org hk --brief ...` → task completes |

### 10.3 Existing tests touched

- `tests/daemon/*` — every per-org route test gains `--org <slug>` in the
  path; fixtures shift from "one runtime" to "one container, one org".
- `tests/test_runtime.py` — `schema_version: 2` semantics; rejection of
  v1 layouts.
- `tests/test_workspace_adapters.py` — `{ORG_SLUG}` substitution
  assertions; per-org workspace paths.
- `tests/test_prompt_loader.py` — readers operate on `<org-root>/org/...`
  paths instead of `<runtime>/org/...`.
- `tests/integration/*` — daemon fixtures rebuild around the new layout;
  fake Claude / Codex binaries gain a per-call assertion that the `--org`
  arg matches the test's expected slug.

### 10.4 Test fixture refactor

```python
def make_container(tmp_path: Path) -> RuntimeDir:
    """Build an empty multi-org runtime container with v2 marker."""
    ...

def make_org(container: RuntimeDir, slug: str = "test-org") -> Path:
    """Create <container>/orgs/<slug>/ with a populated org/ skeleton."""
    ...

def make_org_state(org_root: Path, settings: Settings) -> OrgState:
    """Build an OrgState directly for tests that bypass the daemon lifespan."""
    ...
```

### 10.5 Verification before completion

- `uv run pytest tests/ -v` (unit) green.
- `uv run pytest tests/ -v -m integration` green; the **two-orgs-concurrent**
  test is the new red line — without it, regressions to a global lock or
  shared queue won't surface.
- Manual smoke: bootstrap a fresh runtime, init two orgs, submit a real
  task to each in parallel using the dev Claude binary, observe both
  `opc tail` streams making progress simultaneously.

## 11. Out of scope (revisited)

- Per-org auth tokens / RBAC.
- Per-org `Settings` env knobs (e.g., per-org `OPC_PERMISSION_MODE`).
- Cross-org KB sharing or audit aggregation.
- Per-org rate limiting; per-org soft cap on the global worker pool.
- Hard deletion of an org folder via CLI.
- Multi-container orchestration UX (still single-active in `runtimes.yaml`).
- An `opc orgs delete <slug>` command. Deletion is `rm -rf` by hand,
  founder-only, off the CLI.

## 12. Open questions

None at design time — all foundational decisions resolved during
brainstorming. Implementation details (FastAPI route file layout,
exact migration script location, fixture API surface) are deferred to
the implementation plan.
