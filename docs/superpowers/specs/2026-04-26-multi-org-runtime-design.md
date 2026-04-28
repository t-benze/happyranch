# Multi-Org Runtime Design

**Status:** Draft
**Author:** Founder + Claude
**Date:** 2026-04-26

## 1. Problem

`protocol/` today mixes two unrelated concerns:

- **System kernel** — workflow skills, completion-report contract, KB schema,
  runtime/orchestrator design docs. Org-agnostic.
- **Org content** — HK/Macau tourism charter, agent system prompts with
  jurisdiction-specific clauses, escalation thresholds, brand voice, team
  layout. Specific to one org.

Several pieces of code reach across that mixed boundary and bake one org into
the system: `prompt_loader._AGENT_SOURCES` hardcodes agent names and points at
specific protocol files; `teams.py::DEFAULT_LAYOUT` hardcodes engineering and
content teams; agent system prompts duplicate the completion contract per
agent; the daemon serves a single active runtime via `state.runtime`.

The goal is to support the OPC system being run by multiple distinct orgs —
each with its own charter, agents, escalation rules, and concurrent task flow
— while keeping the existing HK/Macau setup as a reference example.

## 2. Goals & non-goals

**Goals**

1. The repo's `protocol/` contains system-kernel content only — no agent
   names, no jurisdictions, no thresholds, no team layout.
2. Each org runtime owns its agents, charter, escalation rules, and team
   layout under `<runtime>/org/`.
3. The daemon serves all registered orgs concurrently. Two orgs can run
   tasks in parallel.
4. Every CLI command takes an explicit `--org <slug>`. No active-org
   concept, no env var, no file.
5. The HK/Macau tourism org survives unchanged in semantic terms — it just
   moves from `protocol/` to a runtime + an in-repo example.

**Non-goals**

- Per-org auth tokens. Single shared bearer token stays.
- Per-org `Settings` (env-var-driven config knobs). All orgs share process
  config for now.
- Cross-org KB sharing.
- Per-org rate limiting.
- `opc.yaml` schema versioning beyond `schema_version: 1`.
- Backwards compatibility for the dev runtime past the migration step. This
  is a hard cut.

## 3. Architecture summary

- **Repo `protocol/`** — system kernel only:
  - `skills/` (start-task, make-worktree, manage-repo, manage-agent, talk).
  - `00-completion-contract.md` (NEW) — universal completion-report format,
    EH decision schema, agent-callback command list.
  - `05-runtime-blueprint.md`, `05b/c/e-*.md`, `06-knowledge-base.md` —
    runtime/orchestrator/dashboard/KB design docs, lightly de-orged.
- **Repo `examples/orgs/hk-macau-tourism/org/`** — populated `org/` tree
  matching the runtime shape, used as the source for `opc init --from`.
- **Each runtime** — `<runtime>/org/` holds all editable org content
  (`charter.md`, `escalation-rules.md`, `teams.yaml`, `agents/<name>.md`,
  `agents/_pending/<name>.md`).
- **Daemon `DaemonState`** — registry of `OrgState`s keyed by slug. Each
  `OrgState` owns its DB, queue, event bus, sessions, and locks.
- **API surface** — every per-org route is path-prefixed `/orgs/<slug>/...`.
  Cross-org registry routes (`/orgs`) operate on `~/.opc/runtimes.yaml`.
- **CLI surface** — every per-org command requires `--org <slug>`. The
  registry-management commands (`opc init`, `opc orgs list`,
  `opc orgs unregister`) take no `--org`.
- **Agent identity** — no built-in names in code. `_AGENT_SOURCES` deleted.
  `DEFAULT_LAYOUT` deleted. Agents are pure data, loaded from the runtime.
- **Enrollment** — files-only. `_pending` subdirectory holds awaiting-approval
  agents; approval is `os.replace` to the active directory plus workspace
  bootstrap. The `agent_enrollments` table is dropped.

## 4. Repo layout after the cut

### 4.1 `protocol/` (system kernel)

```
protocol/
├── skills/
│   ├── start-task/
│   ├── make-worktree/
│   ├── manage-repo/
│   ├── manage-agent/
│   └── talk/
├── 00-completion-contract.md        # NEW
├── 05-runtime-blueprint.md          # was 05-team-blueprint.md, retitled
├── 05b-agent-runtime.md
├── 05c-orchestrator.md              # de-orged: HK/Macau examples → generic
├── 05e-dashboard.md                 # de-orged
└── 06-knowledge-base.md
```

Removed from `protocol/` and not replaced in-repo (the canonical content
moves to `examples/orgs/hk-macau-tourism/org/`):

- `01-org-charter.md`
- `02-system-prompts-managers.md`
- `03-system-prompts-workers.md`
- `04-escalation-rules.md`
- `05a-teams.md`

### 4.2 `examples/orgs/hk-macau-tourism/`

```
examples/orgs/hk-macau-tourism/
└── org/
    ├── charter.md                   # was protocol/01-org-charter.md
    ├── escalation-rules.md          # was protocol/04-escalation-rules.md
    ├── teams.yaml                   # engineering + content layout
    └── agents/
        ├── engineering_head.md
        ├── content_manager.md
        ├── product_manager.md
        ├── dev_agent.md
        ├── payment_agent.md
        ├── qa_engineer.md
        ├── content_writer.md
        └── content_qa.md
```

`opc init <path> --slug X --from examples/orgs/hk-macau-tourism` copies this
`org/` tree verbatim into `<path>/org/`.

### 4.3 `protocol/00-completion-contract.md` (extracted)

Holds the contract content currently duplicated inside every agent's system
prompt:

- The `## Task completion report` format (Confidence, Risks flagged,
  Dependencies, Suggested reviewer focus).
- The EH decision schema (`{action: "delegate"|"done"|"escalate", ...}`).
- The agent-callback command list (`opc report-completion`, `opc learning`,
  `opc manage-repo`, `opc manage-agent`, `opc kb add`).

The orchestrator's prompt-builder appends this block to each agent's
session-time system prompt, removing the duplication and letting the contract
evolve as one unit.

## 5. Runtime org folder shape

```
<runtime>/
├── opc.yaml                         # marker; gains `slug`, `created_at`,
│                                    # `schema_version`
├── opc.db                           # per-org SQLite (audit, scorecards,
│                                    # tasks, kb meta, talks)
├── org/                             # NEW — all editable org content
│   ├── charter.md                   # reference doc; humans edit, no code parses
│   ├── escalation-rules.md          # reference doc; humans edit, no code parses
│   ├── teams.yaml                   # MOVED from <runtime>/teams.yaml
│   └── agents/
│       ├── <name>.md                # approved
│       └── _pending/
│           └── <name>.md            # awaiting founder approval
├── workspaces/<agent>/...           # unchanged
├── kb/...                           # unchanged
└── talks/...                        # unchanged
```

### 5.1 `opc.yaml` marker

```yaml
slug: hk-tourism
created_at: 2026-04-26T10:00:00Z
schema_version: 1
```

`slug` must match the entry in `~/.opc/runtimes.yaml`. Daemon validates on
startup and refuses to register if they disagree. `RuntimeDir.slug` becomes a
property reading from this file (caches in-process).

### 5.2 Per-agent file format

`<runtime>/org/agents/<name>.md` (and `_pending/<name>.md`):

```markdown
---
name: dev_agent
team: engineering
role: worker            # worker | manager
executor: claude        # claude | codex
allow_rules: []         # extra Bash prefixes (besides baseline `opc`)
repos:
  my-opc: https://github.com/t-benze/my-opc.git
enrolled_by: engineering_head      # null for founder-authored
enrolled_at_task: TASK-042         # null for founder-authored
enrolled_at: 2026-04-15T08:00:00Z  # null for founder-authored
---

You are the Dev Agent. Your responsibilities are...

[full system prompt body, role-specific only — completion contract appended
 by the orchestrator at session-build time]
```

**Required frontmatter fields:** `name`, `team`, `role`, `executor`. All
others optional with defined defaults (`allow_rules: []`, `repos: {}`,
`enrolled_*: null`). Body is required and non-empty.

**Validation rules:**

- `name` must match the filename stem.
- `name` must be `^[a-z0-9_]{1,64}$`.
- `team` must exist in `org/teams.yaml`.
- `role` must be `worker` or `manager`. Manager files must list the agent's
  name as `manager` for the team in `teams.yaml`. Worker files must list it
  in the team's `workers`.
- `executor` is `claude` or `codex`.
- `allow_rules` items are kebab-friendly Bash prefix strings (no `Bash(...)`
  wrapping — that's added by `_format_allow_rule`).
- `repos` keys match `^[a-z0-9-]{1,32}$`; values are URL strings.
- `enrolled_at` is RFC 3339 if present.

Validation failure on read raises `AgentParseError` with the file path and
specific cause; the API translates to a 400 / startup abort as appropriate.

### 5.3 Pending → approved transition

- **Enroll (EH):** `opc manage-agent --org X --from-file payload.json` →
  daemon writes `<runtime>/org/agents/_pending/<name>.md` atomically.
- **Approve (founder):** `opc approve-agent --org X <name>` →
  `os.replace(_pending/<name>.md, <name>.md)` → bootstrap workspace (clone
  repos, write CLAUDE.md/AGENTS.md, .claude/settings.json, copy skills) →
  audit-log `agent_approved`.
- **Reject (founder):** `opc reject-agent --org X <name>` →
  `os.unlink(_pending/<name>.md)` → audit-log `agent_rejected`.

The file move is atomic on POSIX, so status transitions cannot tear.
Bootstrapping is best-effort after the move; if it fails, the file is
already in the active directory and the founder can retry via
`opc init-agent --org X <name>`.

### 5.4 What lives where (loader rules)

- `org/agents/*.md` (excluding `_pending/`) → active agents.
- `org/agents/_pending/*.md` → pending agents.
- Anything else under `org/` (charter.md, escalation-rules.md, teams.yaml)
  → no code reads it via the loader. `teams.yaml` is read by `TeamsRegistry`.
  `charter.md` and `escalation-rules.md` are reference documents only.

## 6. Daemon multi-tenancy

### 6.1 `OrgState` (new)

```python
@dataclass
class OrgState:
    slug: str
    runtime: RuntimeDir
    db: Database
    queue: TaskQueue
    event_bus: EventBus
    sessions: SessionTracker
    teams: TeamsRegistry
    db_lock: asyncio.Lock
    kb_lock: asyncio.Lock
```

One `OrgState` per registered runtime. Cross-org leakage is structurally
impossible because each org owns distinct instances; the only shared state
is the registry map.

### 6.2 `DaemonState` becomes a registry

```python
class DaemonState:
    def __init__(self, *, settings: Settings, ...):
        self._orgs: dict[str, OrgState] = {}
        self._registry_lock = asyncio.Lock()
        self.settings = settings              # process-global, shared
        self.event_loop: asyncio.AbstractEventLoop
        self.bind_host: str
        self.port: int

    async def add_org(self, runtime: RuntimeDir) -> OrgState: ...
    async def remove_org(self, slug: str) -> None: ...
    def get_org(self, slug: str) -> OrgState: ...   # raises 404 if missing
```

### 6.3 Lifespan

- **Startup:** read `~/.opc/runtimes.yaml`. For each entry: load `RuntimeDir`,
  validate `opc.yaml.slug == registry slug`, build `OrgState`, start its
  worker pool.
- **Shutdown:** for each `OrgState` in parallel: drain queue, close DB, stop
  workers. Bounded shutdown timeout shared across orgs.

### 6.4 Routes

All current per-org routes move under `/orgs/{slug}/...`:

```
POST   /orgs/{slug}/tasks
GET    /orgs/{slug}/tasks
GET    /orgs/{slug}/tasks/{id}
GET    /orgs/{slug}/tasks/{id}/events       (SSE)
POST   /orgs/{slug}/tasks/{id}/resolve-escalation
POST   /orgs/{slug}/tasks/{id}/revisit
POST   /orgs/{slug}/tasks/.../callback...   (agent-side)
GET    /orgs/{slug}/agents
POST   /orgs/{slug}/agents/init             (SSE)
POST   /orgs/{slug}/agents/manage
POST   /orgs/{slug}/agents/{name}/learnings
POST   /orgs/{slug}/agents/{name}/repos
GET    /orgs/{slug}/agents/enrollments
POST   /orgs/{slug}/agents/{name}/approve
POST   /orgs/{slug}/agents/{name}/reject
GET    /orgs/{slug}/audit
GET    /orgs/{slug}/kb
... [all kb/talks routes]
```

Cross-org routes (no slug):

```
GET    /orgs                                # list registered orgs
POST   /orgs                                # register (= opc init)
DELETE /orgs/{slug}                         # unregister (does NOT delete folder)
GET    /health
```

### 6.5 `OrgDep` dependency

```python
def resolve_org(slug: str, state: DaemonState = Depends(get_state)) -> OrgState:
    return state.get_org(slug)

OrgDep = Annotated[OrgState, Depends(resolve_org)]
```

Every per-org route signature becomes:

```python
@router.post("/orgs/{slug}/tasks")
async def create_task(slug: str, body: TaskCreate, org: OrgDep, ...):
    org.db.create_task(...)
    org.queue.enqueue(...)
```

The `Depends(verify_token)` runs first so unauthed requests never hit the
registry.

### 6.6 Worker pool sizing

Each `OrgState` runs its own worker pool, default size from
`OPC_WORKER_POOL_SIZE` (process-global). Two busy orgs double total
in-flight tasks. A global cap is out of scope; can be added later if needed.

## 7. CLI surface

### 7.1 Registry management (no `--org`)

| Command | Purpose |
|---|---|
| `opc init <path> --slug <slug> [--from <example-path>]` | Create runtime, register, optionally seed `org/` from an example |
| `opc orgs list` | List registered orgs (slug, path, valid/invalid) |
| `opc orgs unregister <slug>` | Remove from `runtimes.yaml`; does **not** delete folder |

### 7.2 Per-org commands (require `--org`)

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
opc backfill-enrollments --org <slug>      # founder recovery; TTY-gated
opc resolve-escalation --org <slug> --task-id ... --decision ... --rationale "..."
opc revisit --org <slug> TASK-052 [--note "..."]
opc kb {list|get|add|update|delete|reindex|precedent|search} --org <slug> ...
opc talk {start|end|abandon|resume|status|list|show} --org <slug> ...
opc recall --org <slug> TASK-001 [--tree] [--fetch-artifact <relpath>]
```

Removed: `opc use`.

### 7.3 Agent-side callbacks

Every callback the start-task / manage-* skills emit gains `--org <slug>`.
The slug is injected at workspace-build time via skill template substitution.

```
opc report-completion --org <slug> --task-id ... --session-id ... --status ... --from-file ...
opc learning --org <slug> --agent <you> --session-id ... --task-id ... --text "..."
opc manage-repo {add|remove|update} --org <slug> --agent <you> ...
opc manage-agent --org <slug> --from-file ...
opc kb add --org <slug> --agent <you> --from-file ...
opc kb update --org <slug> --agent <you> --from-file ...
opc kb delete --org <slug> --agent <you> --confirm [--as-founder]
opc kb precedent --org <slug> --task-id ... --decision ... --rationale "..." --as-founder
```

The agent doesn't infer the slug; the orchestrator injects it as a literal
into the prompt and into every skill file copied into `.claude/skills/`.

### 7.4 Skill template substitution

Today skills are copied verbatim from `protocol/skills/` into
`<runtime>/workspaces/<agent>/.claude/skills/`. After the change, the copy
substitutes `{ORG_SLUG}` placeholders in skill source with the literal slug.

`opc init-agent --org X` re-runs the copy on every invocation, so updating a
skill in `protocol/` and re-running `init-agent` is the supported way to
roll a skill change.

### 7.5 Error UX for missing `--org`

```
$ opc tasks
error: --org <slug> is required
registered orgs:
  hk-tourism    /Users/tangbz/runtimes/hk-tourism
  internal-org  /Users/tangbz/runtimes/internal
```

Same hint on every per-org subcommand.

### 7.6 Allow-rules

Baseline `Bash(opc:*)` (settings.json) and `Bash(opc *)` (CLI flag) match
all `opc` invocations regardless of suffix. The `--org <slug>` argument
doesn't widen or narrow the allow-rule surface; no change needed.

## 8. Prompt loader & enrollment internals

### 8.1 New `prompt_loader` API

```python
@dataclass(frozen=True)
class AgentDef:
    name: str
    team: str
    role: Literal["worker", "manager"]
    executor: Literal["claude", "codex"]
    allow_rules: tuple[str, ...]
    repos: dict[str, str]
    enrolled_by: str | None
    enrolled_at_task: str | None
    enrolled_at: datetime | None
    system_prompt: str

def agents_dir(runtime: RuntimeDir) -> Path
def pending_dir(runtime: RuntimeDir) -> Path

def load_agent(runtime: RuntimeDir, name: str) -> AgentDef | None
def list_agents(runtime: RuntimeDir) -> list[AgentDef]
def list_pending(runtime: RuntimeDir) -> list[AgentDef]
def write_pending_agent(runtime: RuntimeDir, agent: AgentDef) -> Path
def approve_agent(runtime: RuntimeDir, name: str) -> AgentDef
def reject_agent(runtime: RuntimeDir, name: str) -> None

def allow_rules_for_agent(
    runtime: RuntimeDir, name: str | None, *, cli: bool,
) -> list[str]
```

`_AGENT_SOURCES` is deleted. Every existing caller of `load_system_prompt`
or `load_all_prompts(protocol_dir)` migrates to `load_agent(runtime, name)`
or `list_agents(runtime)`.

### 8.2 Frontmatter parser

YAML frontmatter delimited by `^---\n` ... `^---\n`. Body is everything
after the closing fence. Implementation pattern matches the existing
`kb_store.py` parser (the project already has working YAML-frontmatter
parsing for KB entries).

### 8.3 Completion-contract appending

The orchestrator's prompt-builder, when constructing an agent's session
prompt:

```python
def build_session_prompt(agent: AgentDef, contract: str) -> str:
    return f"{agent.system_prompt.rstrip()}\n\n{contract}"
```

`contract` is loaded once from `protocol/00-completion-contract.md` and
cached in the orchestrator. Different sub-blocks for managers vs workers
(EH decision schema is manager-only); the contract file is structured so
the prompt-builder picks the right slice based on `agent.role`.

### 8.4 Loader cache (deferred)

A small `(runtime_path, name, mtime) -> AgentDef` LRU cache is straightforward
to add but not required for v1. Decision: ship without; add only if profiling
shows session-build is bottlenecked here.

### 8.5 Workspace adapter changes

`src/orchestrator/workspace_adapters.py`:

- `allow_rules_for_agent(settings, agent_name, cli=...)` → takes a
  `RuntimeDir` instead of `Settings.get_protocol_dir()`. The DB-fallback
  branch is removed.
- `PersistentWorkspaceSetup` gains a `slug: str` for skill template
  substitution.
- Skill-copy step does template substitution on each file: every
  occurrence of `{ORG_SLUG}` becomes the literal slug. Skills with no
  placeholder copy unchanged.

## 9. Migration plan

### 9.1 `opc migrate-to-org-runtime` (one-shot script)

Invocation:

```
uv run opc migrate-to-org-runtime <runtime-path> --slug <slug> --i-have-a-backup [--apply]
```

`--i-have-a-backup` is mandatory. Without `--apply` the script is dry-run
and prints all intended actions. Without `--i-have-a-backup` the script
aborts before doing anything.

### 9.2 Steps (dry-run prints all; `--apply` executes)

1. **Validate prerequisites.** Path is a valid `opc.yaml` directory.
   `~/.opc/runtimes.yaml` has an entry for the path. Either `opc.yaml` is
   empty or has `slug:` matching `--slug`.
2. **Detect already-migrated.** If `<runtime>/org/teams.yaml` exists OR
   `agent_enrollments` table is absent, report "already migrated" and exit
   0 without changes.
3. **Write `opc.yaml`.** Add `slug`, `created_at` (now if missing),
   `schema_version: 1`.
4. **Create `<runtime>/org/` skeleton.** `mkdir -p org/agents/_pending`.
5. **Move `<runtime>/teams.yaml` → `<runtime>/org/teams.yaml`.** `os.rename`.
6. **Export approved enrollments to files.** For each row in
   `agent_enrollments` with `status='approved'`:
   - Build `AgentDef` from row.
   - Look up `team` in the new `org/teams.yaml`.
   - Look up `role`: manager if `is_team_manager(name)` else worker.
   - **Detect & strip completion-contract block** from `system_prompt` body
     (string match on the canonical `## Task completion report` section). If
     not found, keep body as-is.
   - Write `<runtime>/org/agents/<name>.md`.
7. **Export pending enrollments to files.** Same as 6 but rows with
   `status='pending'` go to `_pending/<name>.md`. Rejected rows are
   skipped.
8. **Drop `agent_enrollments` table.**
9. **Bump SQLite `user_version`.**
10. **Print summary.** Counts and paths.

### 9.3 Founder-authored agents are not auto-seeded

Agents that today live in `protocol/02..03-*.md` (engineering_head,
content_manager, dev_agent, etc.) and have no `agent_enrollments` row are
**not** migrated by the script. After running migration, the founder
repopulates `<runtime>/org/agents/` manually for whichever agents they
want to keep:

```
cp examples/orgs/hk-macau-tourism/org/agents/<name>.md <runtime>/org/agents/<name>.md
```

This works because the example tree is built from those very same protocol
files in this PR — the content is identical.

### 9.4 Repo-side migration (same PR)

- Delete `protocol/01-org-charter.md`, `02-system-prompts-managers.md`,
  `03-system-prompts-workers.md`, `04-escalation-rules.md`,
  `05a-teams.md`.
- Create `examples/orgs/hk-macau-tourism/org/charter.md`,
  `escalation-rules.md`, `teams.yaml`, and `agents/<name>.md` for all 8
  agents currently defined in protocol markdown. Content extracted from
  the deleted protocol files.
- Create `protocol/00-completion-contract.md` with the contract block
  extracted from current per-agent prompts (which is identical across
  agents for the relevant section).
- Rename `protocol/05-team-blueprint.md` → `protocol/05-runtime-blueprint.md`.
- Edit `protocol/05c-orchestrator.md` and `protocol/05e-dashboard.md` to
  remove HK/Macau-specific examples in favor of generic placeholders.

### 9.5 Rollback

None. Hard cut. The PR is reviewable as a single diff and `git revert`
undoes both code and protocol-file moves. The `agent_enrollments` table
drop is the only irreversible part on the runtime side; the founder is
expected to back up the runtime folder before running migration (the
`--i-have-a-backup` flag is the explicit acknowledgment).

## 10. Testing approach

### 10.1 Unit tests

| Area | Coverage |
|---|---|
| `prompt_loader` (rewritten) | `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent` against tmp_path runtime; frontmatter parse edge cases |
| `RuntimeDir` | `teams_config_path` returns `org/teams.yaml`; old-path fallback for one release; `slug` round-trip via `opc.yaml` |
| `Database` | `agent_enrollments` table is dropped; migration is idempotent |
| Migration script | Dry-run vs `--apply` parity; idempotent on already-migrated runtimes; aborts without `--i-have-a-backup` |
| `allow_rules_for_agent` | Reads frontmatter only; no DB fallback |
| Workspace adapters | Skill template substitution; baseline `Bash(opc:*)` unchanged |
| `OrgState` | DB connect/close, queue start/drain, isolation between orgs |
| `resolve_org` | Returns right `OrgState`; 404 on unknown slug |
| `runtimes.yaml` ↔ `opc.yaml` slug validation | Daemon refuses to register a runtime whose `opc.yaml` slug disagrees with the registry |
| All routes | Per-org isolation: org A writes don't appear under org B |
| CLI | Every per-org command rejects missing `--org`; flag flows into API path |

### 10.2 Integration tests (`pytest -m integration`)

| Scenario | Coverage |
|---|---|
| Two orgs concurrent | Spawn daemon, register two orgs, submit a task to each, both run to completion in parallel without DB cross-contamination (uses fake Claude binary) |
| Cross-org isolation | After concurrent run, org A's audit log has no entries from org B's task |
| Migration end-to-end | Stand up old-shape runtime fixture (with `agent_enrollments` populated and `<runtime>/teams.yaml` at root), run migration, then daemon serves it correctly |
| Bootstrap from example | `opc init <tmp> --slug X --from examples/orgs/hk-macau-tourism` → `opc init-agent --org X` (no name) → `opc run --org X --brief ...` → task completes |
| Pending → approved file flow | Submit `manage-agent enroll` from EH session, verify `_pending/<name>.md` exists, approve, verify it moved + workspace bootstrapped, submit a task to that agent |

### 10.3 Existing tests touched

- `tests/test_prompt_loader.py`, `tests/test_prompt_loader_allow_rules.py`,
  `tests/test_workspace_adapters.py`, `tests/test_context_builder.py` —
  rewritten to construct test agent files in tmp_path runtime instead of
  relying on `protocol/02..03-*.md`.
- `tests/daemon/*` — every route test gains `--org <slug>` in the path.
  Single-runtime fixtures become single-org fixtures.
- `tests/test_runtime.py` — covers new `<runtime>/org/` skeleton and
  `teams.yaml` move.

### 10.4 Test fixture refactor

- `make_runtime(tmp_path, slug="test-org")` — builds a populated test
  runtime: `org/` skeleton, `org/teams.yaml`, optional pre-written agent
  files.
- `make_org_state(runtime)` — builds an `OrgState` for tests that call
  routes directly without going through the full daemon lifespan.

### 10.5 Verification before completion

- `uv run pytest tests/ -v` (unit) green.
- `uv run pytest tests/ -v -m integration` green; concurrent-orgs and
  migration tests are the new red lines.
- Manual smoke: bootstrap a fresh runtime from
  `examples/orgs/hk-macau-tourism`, run a real task end-to-end with the
  dev Claude binary.

## 11. Out of scope (explicit non-goals revisited)

- Per-org auth tokens. Single shared `~/.opc/auth_token`.
- Per-org `Settings` (env-var-driven config).
- Cross-org KB sharing.
- Per-org rate limiting / global worker-pool cap.
- Generic-named agent archetypes (template prompts with placeholders).
- Concurrent-multi-tenancy auth boundaries (per-org RBAC).
- `opc.yaml` schema versioning beyond `schema_version: 1`.
- Hard-deletion of a runtime folder via CLI (`opc orgs unregister` only
  edits the registry).

## 12. Open questions

None at design time — all foundational decisions resolved during
brainstorming. Open implementation details (e.g., exact migration script
file location, FastAPI route file split between per-org and cross-org)
are deferred to the implementation plan.
