# Per-Agent Learnings Structural Upgrade — Design Spec

**Date:** 2026-05-13
**Status:** Draft, pending implementation plan.
**Relates to:** `protocol/05b-agent-runtime.md` §2 (Agent Memory Architecture); `protocol/06-knowledge-base.md`; existing flat `learnings.md` written by `src/daemon/routes/agents.py::append_learning`.

## 1. Goal

Replace the flat append-only `learnings.md` with a per-entry structured store under each agent's workspace. Each learning becomes its own markdown file with YAML frontmatter (stable id, tags, topic, cross-references, optional promotion stamp). A regenerated index file replaces the file body in the agent's bootstrap doc, so the bootstrap stays compact even as the corpus grows.

The agent has *already* invented a memory protocol in flat markdown — numbered cross-references (`per learning #17`), explicit "why" + "how to apply" sections, occasional promotion to KB precedents. This upgrade ratifies that protocol with first-class data structures.

## 2. Non-goals

- **Vector / semantic retrieval.** Substring + tag + topic, same scoring shape as `kb_store.search`. Reconsider only when an agent's corpus crosses ~150 entries or the inlined index breaks the bootstrap budget.
- **Cross-agent learning sharing.** Learnings remain private to the owning workspace. Cross-agent durable knowledge has its own channel (KB precedents/reference). The KB is the cross-agent surface; learnings are not.
- **Auto-extraction from chat or session logs.** Learnings are deliberate, agent-authored retrospectives at session-end. No LLM-driven extraction pass.
- **Auto-update / conflict resolution.** Existing learnings are never mutated by the system. Agents update entries themselves via an explicit CLI call; conflicting rules are handled with `supersedes:`, not by overwriting prior wording.
- **Versioned content history.** v1 update overwrites the file. The SQLite audit row captures "agent X updated LRN-NNN at T from task TASK-Y" but not prior wording. If a learning needs to evolve while preserving the old form, the agent uses `supersedes:` on a new entry (same primitive as KB).
- **Git history requirement.** No assumption that the runtime is git-initialized. Founders may `git init` for their own recovery, but the design does not depend on it.
- **Bulk migration tooling for the framework.** Existing flat `learnings.md` files are migrated by a one-shot agent task per workspace (the agent owns its content best); no top-down migration CLI.
- **Founder editing of agent learnings.** Founders intervene through the KB (promotion stamps via `--as-founder`), not by hand-editing agent workspaces.
- **Deletion in v1.** Only update + supersede. Founder-only delete can be added later if a clear need emerges.

## 3. Storage

### 3.1 Layout

Per-agent, inside the existing workspace:

```
<runtime>/orgs/<slug>/workspaces/<agent_name>/
├── agent.yaml
├── CLAUDE.md (or AGENTS.md)
├── task_history.md
├── learnings/
│   ├── _index.md                                 # regenerated
│   ├── LRN-001-gitnexus-hf-mirror.md
│   ├── LRN-017-founder-gate-override.md
│   ├── LRN-042-cross-team-dispatch-forbidden.md
│   └── ...
├── learnings.legacy.md                           # preserved after migration; not read at session start
└── repos/...
```

The old top-level `learnings.md` is renamed to `learnings.legacy.md` during migration and never re-read by the bootstrap. New writes always go to per-entry files. Legacy file stays on disk so the founder can inspect or delete it manually.

### 3.2 ID + slug rules

- **ID:** `LRN-NNN` where `NNN` is a 3-digit zero-padded counter, **per agent**. Allocation policy mirrors task IDs: `MAX(suffix) + 1` over existing files in the directory (consistent with the recent `next_task_id` fix in `86499ff`). Counter never re-uses retired IDs even after supersede.
- **Slug:** kebab-case ASCII, `^[a-z0-9][a-z0-9-]{0,63}$`, same regex as KB. Agent chooses it.
- **Filename:** `<id>-<slug>.md`. Both the `id` and `slug` frontmatter fields must match the filename components. Filename is canonical identity; ID is the durable handle used in cross-references.
- **Why ID-prefixed:** stable IDs survive slug rename or supersede; the agent's existing cross-reference idiom (`per learning #17`) maps cleanly to `LRN-017`.

### 3.3 Entry shape

YAML frontmatter + markdown body, ≤32 KiB body (same cap as KB):

```markdown
---
id: LRN-042
slug: cross-team-dispatch-forbidden
title: Cross-team dispatch forbidden even under founder verbal authorization
topic: workflow-guardrail                # single kebab-case token; drives _index.md grouping
tags: [cross-team, dispatch, founder-authority]
authored_by: engineering_head            # stamped server-side
authored_at: 2026-04-30T15:22:00Z        # stamped server-side
updated_by: engineering_head             # stamped server-side
updated_at: 2026-04-30T15:22:00Z         # stamped server-side
source_task: TASK-235                    # optional; the task that produced the learning
related_to: [LRN-020, LRN-028]           # optional; validated at write time (unknown ID = 400)
supersedes: null                         # optional LRN-NNN of an earlier learning being replaced
promoted_to: null                        # optional KB slug if this learning was promoted to a KB precedent
---

# Cross-team dispatch forbidden even under founder verbal authorization

**Why:** ...

**How to apply:** ...

**Verified at:** TALK-017
```

**Server-owned fields** (`authored_*`, `updated_*`): any value the agent sends is overwritten by the daemon. Author identity is taken from the route's owning agent (path parameter `<agent_name>`), not a separate flag.

**Agent-supplied required fields:** `slug`, `title`, `topic`.
**Agent-supplied optional:** `tags`, `source_task`, `related_to`, `supersedes`.
**Server-only field:** `promoted_to` — written by the `promote` route, never accepted from agent payload.

**Note on `id`:** the agent does NOT supply `id` on `add`. The daemon allocates `LRN-NNN` and stamps it. The agent supplies `id` only on `update` (to identify the target) and `promote`.

### 3.4 `_index.md`

Machine-generated. Regenerated inside the write lock after every successful `add`/`update`/`promote`. Groups entries by `topic` (topics alphabetized), with entries inside each topic sorted by ID descending (newest first):

```markdown
# Learnings Index: engineering_head

_Generated 2026-05-13T12:00:00Z — 62 entries_

## workflow-guardrail (12)

- `LRN-017` — Founder gate-override pattern (bug-small deploy)  [tags: founder-authority]
- `LRN-020` — Talk-time dispatch hard rule  [tags: talk, dispatch]
- `LRN-028` — `opc revisit` requires interactive TTY  [tags: cli, guardrail]
- `LRN-042` — Cross-team dispatch forbidden  [tags: cross-team, dispatch] ↗ promoted: founder-concern-boundary-eh-edition
- ...

## env-trap (8)
...
```

The index entry shows: ID, title, top tags, and a `↗ promoted: <kb-slug>` indicator when `promoted_to` is set. The body is **not** inlined into the index; the index is the bootstrap-shipped surface, full bodies fetched on demand.

Not a source of truth — deleting `_index.md` is safe; the daemon rebuilds it on next write or on explicit `opc learning reindex`.

### 3.5 Validation rules

| Check | Error code | Condition |
|---|---|---|
| `invalid_id` | 400 | `id` does not match `^LRN-\d{3,}$` |
| `invalid_slug` | 400 | slug violates KB slug regex |
| `missing_frontmatter` | 400 | required field missing or wrong type |
| `entry_too_large` | 400 | body > 32 KiB |
| `unknown_related_id` | 400 | any `related_to` entry not present in this agent's `learnings/` |
| `unknown_supersedes` | 400 | `supersedes` ID not present |
| `id_exists` | 409 | filename for that ID already exists on `add` |
| `id_not_found` | 404 | `update`/`promote` target missing |
| `promoted_locked` | 409 | `update` attempted on an entry with `promoted_to` set (use a new learning that supersedes instead) |
| `kb_slug_missing` | 400 | `promote` payload missing `kb_slug` |
| `kb_slug_not_found` | 404 | `promote` payload references a KB slug that does not exist |

Cross-reference validation is **per-agent only** — `related_to: [LRN-017]` resolves against the same agent's directory. There are no cross-agent learning references in v1.

## 4. CLI surface

All under `opc learning`. Agents use the existing baseline `opc` allow rule — no new permission surface.

```
opc learning list   --org <slug> --agent <name> [--topic T] [--tag T] [--promoted | --not-promoted] [--json]
opc learning get    --org <slug> --agent <name> <id-or-slug>
opc learning search --org <slug> --agent <name> <query> [--limit N] [--json]
opc learning add    --org <slug> --agent <name> --from-file <path>
opc learning update --org <slug> --agent <name> <id> --from-file <path>
opc learning promote --org <slug> --agent <name> <id> --kb-slug <slug>
opc learning reindex --org <slug> --agent <name>
```

`--agent <name>` is required everywhere. Agents pass their own name (matches the existing `opc learning` flag convention). The bootstrap doc instructs agents to call only with their own name; the HTTP layer does not enforce this in v1 (see §5 auth note). Cross-agent reads are not exposed in the bootstrap (use the KB for cross-agent durable knowledge); a future per-agent auth pass would harden the boundary at the route level.

### 4.1 `add` — payload (file-based, multi-line via --from-file)

```yaml
slug: cross-team-dispatch-forbidden
title: Cross-team dispatch forbidden even under founder verbal authorization
topic: workflow-guardrail
tags: [cross-team, dispatch, founder-authority]
source_task: TASK-235
related_to: [LRN-020, LRN-028]
supersedes: null
body: |
  **Why:** ...
  **How to apply:** ...
  **Verified at:** TALK-017
```

Daemon allocates `LRN-NNN`, stamps `authored_*`/`updated_*`, validates, writes `learnings/LRN-NNN-<slug>.md`, regenerates `_index.md`. Response: `{id: "LRN-042", path: "learnings/LRN-042-cross-team-dispatch-forbidden.md"}`.

### 4.2 `update` — payload

```yaml
slug: cross-team-dispatch-forbidden                # may rename slug; ID stays
title: Cross-team dispatch forbidden ...
topic: workflow-guardrail
tags: [cross-team, dispatch, founder-authority]
related_to: [LRN-020, LRN-028, LRN-054]
body: |
  ...updated wording...
```

`id` comes from the CLI positional argument, NOT the payload. `authored_*` preserved from existing file; `updated_*` re-stamped. If the slug changes, the file is renamed. Rejected with `promoted_locked` if `promoted_to` is set.

### 4.3 `promote` — payload

```
opc learning promote --org <slug> --agent <name> LRN-042 --kb-slug founder-concern-boundary-eh-edition
```

Daemon: validates the KB slug exists (404 if not), sets `promoted_to: <kb-slug>` on the learning, rewrites the body to a 2-line stub (`See KB precedent: <slug>. Original learning preserved in git history if runtime is git-initialized.`), preserves frontmatter except for the stub-replaced body. Audit row: `learning_promoted`.

This is a one-way operation. There is no `unpromote`. If the founder later deletes the KB precedent, the stub becomes a dangling pointer — surfaced by a `learning_promoted_dangling` row produced by `opc learning reindex` only (not auto-detected on KB delete, to keep the KB code path simple).

### 4.4 `search` — scoring

Identical to `kb_store.search`: title match scores 10, body match scores 5, tag/topic match scores 2. Snippets ~80 chars around the match. Promoted-stub learnings are excluded by default (full KB precedent is what the agent should read instead); pass `--include-promoted` to include them.

## 5. HTTP routes

New endpoints mount under a separate `/entries` sub-namespace to avoid colliding with the legacy single-line endpoint:

Per-org, prefix: `/api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries`:

| Method | Path | Body | Auth |
|---|---|---|---|
| GET | `/` | — | bearer |
| GET | `/{id_or_slug}` | — | bearer |
| POST | `/search` | `{query, limit?}` | bearer |
| POST | `/` | LearningAdd payload | bearer |
| PUT | `/{id}` | LearningUpdate payload | bearer |
| POST | `/{id}/promote` | `{kb_slug}` | bearer |
| POST | `/reindex` | — | bearer |

**Auth note for v1:** the daemon uses a single shared bearer token (`require_token`) with no per-agent identity binding — same model as every other agent route. Agents *cannot* be prevented at the HTTP layer from writing to another agent's learnings; the practical isolation is at the CLI permission layer (the agent's bootstrap doc directs them to call `opc learning ... --agent <self>`, and the `Bash(opc:*)` allow rule constrains the agent's shell surface but does not authenticate identity). This matches the current `append_learning` route's posture. Per-agent identity binding is tracked as a system-wide follow-up that would touch every agent route, not just learnings.

The existing `POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings` (single-line `{text}` append) is **deprecated** but stays mounted for backward compat:

- If the workspace has a `learnings/` directory (post-migration), the legacy endpoint returns 410 Gone with `{migrate_to: "POST /agents/{name}/learnings/entries"}`.
- If the workspace is still pre-migration (no `learnings/` dir), the legacy endpoint continues to append to the flat `learnings.md`.

This lets us land the new code path without breaking unmigrated workspaces; each workspace flips its behavior atomically at migration time. Both endpoints can be removed in a later cleanup pass once every workspace is migrated.

`agent-self` means: the request's bearer token resolves to a session whose agent identity matches the path's `{agent_name}`. The team manager exception on read routes lets managers consult their team members' learnings during decision time — useful future feature, gated behind an explicit team-membership check, NOT used in v1's bootstrap. (Bootstrap still injects only the agent's own index.)

## 6. Bootstrap integration

`ClaudeWorkspaceAdapter._build_sections` (and the Codex/opencode equivalents) currently says:

```
## Persistent Files
- `learnings.md` -- your accumulated operational learnings
- `task_history.md` -- read-only, updated by orchestrator
```

After migration, this becomes:

```
## Persistent Files
- `learnings/_index.md` -- index of your operational learnings (full bodies via `opc learning get`)
- `task_history.md` -- read-only, updated by orchestrator

## Your Learnings
Read the full index inline at session start. Fetch any entry's body with:
  opc learning get --org <slug> --agent <you> <LRN-NNN-or-slug>

Cross-reference rules already accumulated. Write new learnings with:
  opc learning add --org <slug> --agent <you> --from-file <path>
Update existing rules with:
  opc learning update --org <slug> --agent <you> <LRN-NNN> --from-file <path>
Promote durable, cross-agent rules to KB precedents with:
  opc learning promote --org <slug> --agent <you> <LRN-NNN> --kb-slug <slug>
```

The bootstrap adapter also **inlines the contents of `_index.md`** under "Your Learnings" so the agent sees the full index without an extra CLI call. Estimated size for engineering_head at 60 entries: ~5 KB (vs ~42 KB today inlining the full file).

The `start-task` skill is updated to point at `opc learning list --tag <topic>` as the "consult prior learnings" step, parallel to the existing "Consult KB" step.

## 7. Migration

### 7.1 Per-workspace, opt-in, agent-driven

Migration is a one-shot task the founder dispatches per agent:

```
opc run --org <slug> --team <team> \
  --brief-file - <<EOF
Migrate your existing learnings.md to the new structured layout.

For every entry in learnings.md:
1. Allocate an LRN-NNN ID (sequential).
2. Author a slug + title + topic + tags.
3. Map any "per learning #N" cross-reference to its new LRN-NNN ID.
4. Identify entries already promoted to KB precedents (e.g. doc-only-changes-verify-symbol-existence) and set promoted_to:.
5. Call `opc learning add --from-file ...` for each entry.
6. When all entries are filed and the new _index.md exists, rename learnings.md to learnings.legacy.md (the daemon will not read it).
7. Stop. Do not delete learnings.legacy.md — that's the founder's call.
EOF
```

The agent runs this in its own session and dispatches add-after-add. The orchestrator does NOT pre-process the file. Cross-reference validation triggers reorder mistakes (`unknown_related_id` 400) which the agent fixes by ordering adds correctly (topologically by `related_to`).

### 7.2 Atomic-flip semantics

Migration is "done" for a workspace the moment the `learnings/` directory exists AND `learnings.md` has been renamed to `learnings.legacy.md`. From that point:
- The deprecated `POST .../learnings` (single-line `text`) returns 410.
- The new structured routes are live.
- The bootstrap adapter inlines `learnings/_index.md` instead of `learnings.md`.

Workspaces without a `learnings/` directory continue to use the old code path. There is no global flip-day.

### 7.3 Default for new workspaces

`PersistentWorkspaceSetup.ensure` is updated to create `learnings/` with a starter `_index.md` for any new workspace. New agents never see the flat `learnings.md`.

## 8. Schema + audit changes

### 8.1 Database

Only one new audit verb; no new tables.

| Verb | Payload |
|---|---|
| `learning_added` | `{agent, id, slug, topic, tags, source_task}` |
| `learning_updated` | `{agent, id, slug_changed, fields_changed}` |
| `learning_promoted` | `{agent, id, kb_slug}` |

Existing single-line `learning` audit row (from `append_learning`) stays as-is for the legacy code path.

### 8.2 No additions to `tasks`, `task_results`, `scorecards`, or the workspace adapter contracts beyond the bootstrap text change in §6.

## 9. Module layout

New code lives in `src/infrastructure/learnings_store.py`, paralleling `kb_store.py`:

```python
# src/infrastructure/learnings_store.py
@dataclass
class LearningEntry: ...           # id, slug, title, topic, tags, body, ... (mirrors KBEntry)

class LearningsStore:
    def __init__(self, agent_workspace: Path): ...
    def path_for(self, id: str) -> Path: ...
    def next_id(self) -> str: ...                # MAX(suffix)+1
    def write_entry(self, entry, agent) -> LearningEntry: ...
    def read_entry(self, id_or_slug) -> LearningEntry: ...
    def list_entries(self, topic=None, tag=None, promoted=None) -> list[LearningSummary]: ...
    def update_entry(self, id, entry, agent) -> LearningEntry: ...
    def promote(self, id, kb_slug, agent) -> LearningEntry: ...
    def search(self, query, limit=20, include_promoted=False) -> list[LearningSearchHit]: ...
    def regenerate_index(self) -> None: ...
```

Constructed once per agent workspace inside the route handler. Per-workspace `RLock` on the daemon side serializes writes (mirrors `state.kb_lock` per org → per-(org, agent) lock for learnings).

Routes mount in `src/daemon/routes/agents.py` next to the existing `append_learning` handler (or a new `learnings_v2.py` if the file gets long).

CLI subcommands extend `src/cli.py` `cmd_learning` from a single `add`-only function to a verb-dispatched parser (`list`, `get`, `search`, `add`, `update`, `promote`, `reindex`).

Bootstrap adapter changes in `src/orchestrator/workspace_adapters.py` (`_build_sections` + a new helper that inlines `_index.md` when present).

## 10. Testing

| Layer | Tests |
|---|---|
| `learnings_store.py` | ID allocation under concurrent writes; slug regex; round-trip frontmatter; `_index.md` regeneration grouping; cross-ref validation; `supersedes`/`promoted_to` flags; 32 KiB cap |
| Routes | agent-self auth; team-manager read access; 410 on legacy endpoint when `learnings/` exists; promote → KB-slug lookup; promote idempotency (second promote returns existing record) |
| CLI | `--from-file` parse for `add`/`update`; `LRN-NNN` positional resolution for `get` and `update`; `list --tag` filter |
| Integration | end-to-end add → list → get → update → promote on a real daemon with a fake KB precedent in place; bootstrap regeneration switches between `learnings.md` and `learnings/_index.md` based on workspace state |
| Migration | starter `_index.md` created on fresh workspace; legacy endpoint serves pre-migration workspaces and 410s post-migration ones |

Run via the existing `uv run pytest tests/ -v` and `... -m integration` for the daemon-spawning suite.

## 11. Open follow-ups (out of scope for v1)

- **Cross-agent learning read** for team managers during decision-making. Routes already support it (§5); bootstrap injection deferred until there's a concrete consumer (e.g. team manager prompts that surface "your worker's prior learnings on this topic").
- **Embedding-backed semantic search** when an agent's corpus crosses ~150 entries or inlined index exceeds budget.
- **Founder-only delete** if dead/wrong learnings accumulate faster than supersede chains can hide them.
- **Auto-detect dangling `promoted_to`** when a KB precedent is deleted (today only `reindex` surfaces it).
- **Cross-agent `related_to` references** — would require KB-style cross-workspace ID resolution; not needed in v1.
