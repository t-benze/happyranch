# Task-Brief File Attachments — Design Spike

**Date:** 2026-07-19
**Status:** DESIGN-ONLY. **Build is gated on founder sign-off of THIS design.** No
implementation, no schema migration, no daemon route, and no protocol/ edit may
land until the founder approves the design below — see §13 (Open founder sign-off
items).
**Origin:** THR-109. Founder stated brief-as-pure-text is insufficient and asked to
attach files — especially images (mockups) — when creating a task. Founder ruling
(seq3): a founder-attached file must be reachable by the **assigned agent AND every
sub-agent it delegates to down the task tree** (she attaches a mockup to a task that
lands on the EM; the dev the EM hands it to must also see it). Founder ruling
(seq14/15): durable task-brief attachment bytes require a **dedicated, task-scoped
private file store/root**, not reuse of the org-wide shared artifact store.
**Author:** engineering_manager (design spike; founder is the reviewer/approver).
**Relates to:**
- `runtime/orchestrator/orchestrator.py` — the brief → prompt build (`brief: {brief}`
  line, ~L389) and the per-session executor cwd (`workspaces_dir / agent_name`,
  ~L526). The materialization seam (§7) hooks here.
- `runtime/orchestrator/executors.py` — `ClaudeExecutor` passes the full prompt via
  the CLI `-p` arg (~L663-679). An image cannot ride this text channel (§3).
- `runtime/infrastructure/artifact_store.py` — the existing org-shared artifact store
  remains available for shared cross-task artifacts, but is **not** the durable backing
  for private task-brief attachment bytes (§4 / §6).
- New task-attachment storage seam — a dedicated task-scoped private file store/root
  for durable task-brief attachment bytes (§6). Exact module/path is an implementation
  detail gated on founder sign-off.
- `runtime/infrastructure/database.py` — `tasks` table (~L459-474: `brief TEXT NOT
  NULL`, `parent_task_id`, no attachment column); the thread-attachment sibling
  tables `thread_message_attachments` (~L665-678) and `thread_scoped_attachments`
  (~L682-694) this design is modeled after (§6).
- `runtime/daemon/routes/tasks.py` — `POST /tasks` (~L84-141, body `{team, brief,
  owner}`). The upload/reference surface (§7b) extends this contract.
- `runtime/models.py` — `TaskRecord` (~L71-81), which gains a read-only
  `attachments` view (§6).
- `docs/superpowers/specs/2026-06-09-thread-file-attachments-design.md` — the
  **template**: artifact-backed, reference-only routes, upload-before-reference
  client workflow (TASK-1616). Task attachments are the **task-scoped sibling** of
  thread attachments.
- `protocol/05b-agent-runtime.md` — agent execution, memory & lifecycle; the
  **session-spawn materialization** surface. Build must add a doc-parity paragraph
  here in the same PR (§7c / §13). **Not edited now** — the delta is specified, not
  applied.

---

## 1. Goal

Let **both the founder AND agents attach files — especially images (mockups) — when
creating/dispatching a task**, and guarantee those files are readable by the assigned
agent **and by every descendant task** the agent spawns down the tree, delivered as
real files the executor can `Read` plus an `Attachments:` block naming each file and
its on-disk path in the brief prompt.

**Agent-attach is IN v1** (founder ruling seq8: tasks are normally *agent-dispatched*,
so agents must be able to attach). Founder ruling seq14/15 changes the storage model:
durable bytes live in a **new task-attachment private store/root**, and the DB stores
metadata + pointers into that store. The existing org artifact store is **not** reused
as the byte backing for private task-brief files.

Agent-originated task attachments must use the **new task-attachment API/CLI upload
path** (for example, a `happyranch tasks ... --attach` / `task-attachments put` style
flow, final command shape deferred to build). That path may only perform the same kind
of explicit upload the user/agent requested; it must **not** grant arbitrary host-path
reads or a new ambient filesystem capability. If implementing this requires any new
agent permission-model surface, stop and escalate (§10 / §13). Two covered cases:

- **(a) DELEGATION** — a manager's sub-tasks **inherit** the parent's attachments
  automatically via up-the-tree resolution (§8); the manager does **not** re-attach.
  This is the founder's seq3 case (she attaches a mockup on the EM's task; the dev the
  EM delegates to sees it without re-attaching).
- **(b) ORIGINATION** — an agent starting a task with a **new file of its own**
  uploads that file through the new task-attachment API/CLI path, then passes the
  resulting task-attachment ref on dispatch — the same task-private reference path the
  founder uses.

**The retained guard (invariant, §14):** *attach* means "explicitly upload bytes into
the task-attachment private store, then reference the resulting task-attachment key" —
it is **NOT** a new capability to read arbitrary absolute host filesystem paths. This
design does **not** widen what an agent can reach off the host disk. If any
implementation step would need a **new local-path-read capability** or any
**auth/permission-model change**, the implementer **must STOP and escalate**
(founder-gated).

## 2. Motivation & anchor use cases

Today a brief reaches a worker as **pure text**: `orchestrator.py` builds the prompt
with a `brief: {brief}` line (~L389) and passes the whole prompt to the executor via
the CLI `-p` arg (`executors.py` ~L663-679). There is **no channel for a file**, and
crucially **no per-task working dir** — executors run with cwd =
`workspaces_dir/<agent_name>` (agent-scoped and persistent, `orchestrator.py` ~L526),
so there is nowhere task-specific to drop a file for the session to read.

Anchor use cases:

| # | Actor | Attaches | Expected reach |
|---|-------|----------|----------------|
| A | founder | a UI mockup PNG to a redesign task | the EM who owns the task **and** the frontend_engineer the EM delegates to |
| B | founder | a CSV / PDF spec to a build task | the assigned agent and any dev/qa sub-agents in the subtree |
| C | founder | a screenshot of a bug to a bugfix task | the assigned agent and its reviewer/qa descendants |

All three share the skeleton: *founder attaches at create → bytes live once in the
task-attachment private store → every task in the tree resolves the same attachment set
at spawn → files are materialized to a per-session dir + named in the brief prompt.*

## 3. Why the text channel cannot carry this (grounded)

- **Brief is text-only.** `orchestrator._build_prompt` emits `brief: {brief}` as a
  string; the executor receives it via `-p`. An image is bytes, not text — it cannot
  ride this channel. Attachments must be delivered **out-of-band as files** and only
  **referenced** in the text.
- **No per-task cwd exists.** cwd is `workspaces_dir/<agent_name>` — shared across all
  of that agent's tasks. A per-task/session attachment dir **must be introduced** as
  the materialization target (§7c); we cannot rely on the persistent agent cwd (files
  from task X would leak into task Y).

These two facts are the load-bearing constraints; the rest of the design follows from
them.

## 4. Architecture: a task-scoped private sibling of thread attachments

Task attachments are the **task-tree analogue** of the already-built thread
attachments. The design **reuses the thread-attachment machinery as its template**,
but **does not reuse the org artifact store as durable byte backing**. It adds a
dedicated task-attachment private store/root, exactly one additive metadata table, and
one materialization seam:

| | **Thread attachments** (built) | **Task attachments** (this spec) |
|---|---|---|
| Keyed to | a thread message / a thread | **a task** (`task_id`) |
| Byte store | org artifact store | **dedicated task-attachment private store/root** |
| Metadata table | `thread_message_attachments` / `thread_scoped_attachments` | **new `task_attachments`** |
| Reaches | thread participants | the task **and every descendant** (resolve-up, §8) |
| Delivered as | prompt `Attachments:` block + `artifacts get` | **materialized files** + prompt `Attachments:` block (§7c) |

**Additive-only invariant.** This design **does not alter or drop** any existing
column and **does not overload** any existing semantics — not `audit_log.task_id`
scope-prefixes, not `tasks.blocked_on_job_ids`, not the `brief` column. It gets its
**own** additive metadata table, a **new private storage root**, and **additive** audit
actions (§11). The existing org artifact store remains unmodified for shared
cross-task artifacts and is explicitly **not** the durable store for private task-brief
attachments.

> **Boundary / STOP-and-escalate.** If, during build, the design appears to require
> altering an existing schema column, overloading `audit_log.task_id` scope
> semantics, touching **authentication / the daemon bearer-token flow**, touching
> a **permission-generation surface** (Claude `--allowedTools`, Codex sandbox flags,
> opencode `permission.bash`, the baseline `happyranch` allow-rule), or adding a **new
> local-path-read capability** that would let an agent reference **arbitrary absolute
> host filesystem paths** (rather than bytes explicitly uploaded through the new
> task-attachment API/CLI path — §1 invariant), the implementer **must STOP and
> escalate**. Those are founder-contract surfaces outside EM authority. Agent-attach
> as designed is an explicit upload-and-reference flow; if the implementation drifts
> into ambient host-path reads or a permission generator change, stop. See §10 for the
> open auth/permission questions.

## 5. Where the daemon's job ends (not a decision — the design boundary)

The daemon's responsibility is exactly two steps, and stops there:

1. **Write** each resolved attachment file to a path in the **worker's session dir**
   (the materialization step, §7c).
2. **Name** each file — its on-disk path, size, and content-type hint — in an
   `Attachments:` block injected into the brief prompt (§7c).

From there the **worker** — its agentic CLI, with its own tools — **loads and
processes** the file. The runtime delivers the same by-path attachment +
`Attachments:` block to **every** executor uniformly; there is **no per-executor
special-casing** and
nothing further for the daemon to decide.

> **Footnote (inherent to the worker, not a daemon design choice):** *how much* a
> worker extracts from a given file — especially an image — depends on that CLI's own
> abilities. Claude Code can open and vision-read an image at the path; a non-vision
> CLI sees an opaque file at that path. This is a property of the worker tool, not a
> knob this design sets, so it is **not** a founder sign-off item.

## 6. Data model — a new `task_attachments` table + private store pointer

A dedicated additive table, shaped after `thread_message_attachments`
(`database.py` ~L665) so it inherits the same reference / metadata-snapshot
conventions without touching any existing table. The table stores **metadata and a
pointer into the task-attachment private store**, not durable bytes and not absolute
host paths. **Illustrative** (final DDL is an implementation detail, gated on
sign-off):

```sql
CREATE TABLE IF NOT EXISTS task_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,          -- the task the founder attached to (owning ancestor)
    ordinal INTEGER NOT NULL,       -- display / injection order
    storage_key TEXT NOT NULL,      -- key into the private task-attachment store
    display_name TEXT NOT NULL,     -- UI label / materialized filename basename
    size_bytes INTEGER,             -- metadata snapshot
    content_type TEXT,              -- metadata snapshot (drives image-vs-file hinting)
    uploaded_by TEXT NOT NULL,      -- founder or agent name
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(task_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id);
```

- **Bytes are NOT in the DB and NOT in the org artifact store.** `storage_key` is the
  sole lookup key into the dedicated task-attachment private store/root. The table
  stores metadata only; `size_bytes` / `content_type` are display/durability snapshots.
- **Private store layout.** Recommended root:
  `<runtime>/orgs/<slug>/task-attachments/` (exact path/module deferred to build).
  Recommended keys:
  `task-attachments/<owning-task-id>/<uuid-or-content-key>/<sanitized-basename>`.
  **Read access (seq25, founder-ruled):** any authenticated caller in the same org
  may list/download task attachments via the task-attachment API. No requester
  task/session identity is accepted or required; access is authenticated org-scoped
  bearer. **Materialization at session spawn** resolves own + ancestor attachments
  via `parent_task_id` (task-tree scoped); materialized files are written into the
  spawning task's per-session attachment directory. Cross-org access is denied by
  the existing org-scoped context.
- **Org artifact store boundary.** `<runtime>/orgs/<slug>/artifacts/` remains the
  shared cross-task artifact store used by `happyranch artifacts ...`; it is not the
  durable backing for private task-brief attachments.
- **`tasks` is untouched.** No new column on `tasks`; `brief TEXT NOT NULL` and
  `parent_task_id` are unchanged. `TaskRecord` (`models.py` ~L71-81) gains a
  **read-only** `attachments: list[TaskAttachmentRef]` view populated from the join,
  not a stored column.
- **Additive only.** New table + new read view + additive audit actions (§11). No
  existing column altered or re-meant; no overloaded-column semantics changed. (An
  additive table is within the migration guardrails; a column drop/alter or an
  overloaded-column re-meaning would be founder-gated — none is proposed.)

## 7. Lifecycle

### 7a. Upload-then-reference daemon contract, backed by the private store

Daemon task-create/dispatch routes should remain **reference-oriented**: clients first
upload bytes through the task-attachment upload API/CLI, receiving task-private
`storage_key` / attachment refs, then the task references those keys. This keeps task
creation atomic around metadata rows while keeping durable bytes out-of-band in the
private task-attachment store. The **client** (web/CLI) provides the ergonomic
upload-then-reference workflow.

### 7b. Upload surface — RECOMMENDED: task-attachment upload-then-reference

**Recommendation: add a dedicated task-attachment upload route/CLI command, then
reference the returned task-private keys on `POST /tasks`** — rather than inline
multipart on `POST /tasks` and rather than storing private task-brief files in the
org-wide artifact store.

Rationale: a reference-oriented `POST /tasks` keeps the task-create contract simple and
lets the storage layer enforce cap/name/audit/atomic-write rules before task metadata
is committed. A dedicated task-attachment upload path is needed because founder ruling
seq14/15 makes the bytes private to the task tree; the org artifact store is for shared
cross-task artifacts and is not the durable backing for this feature.

Concretely:
1. Web/CLI uploads each local file through a new task-attachment API/CLI path, getting
   back collision-resistant private `storage_key`s / attachment refs.
2. **`POST /tasks` body gains an optional `attachments: [{storage_key,
   display_name?}]`** array. The daemon validates each referenced key exists in the
   task-attachment private store, is still claimable by the creating task, and has not
   been attached to an unrelated task tree, then writes `task_attachments` rows for the
   new task.
3. Read surface: **`GET /tasks/{id}`** (or the task-detail route) returns the
   `attachments` view for display/download by any authenticated org-scoped bearer
   (seq25) — no requester task/session identity is accepted or required.
   Spawn-time materialization remains task-tree scoped (own + ancestors,
   §7c).

**Contract-drift call-out (MEM-094 / MEM-148):** adding/extending these daemon routes
drifts **BOTH** contract surfaces — the Python OpenAPI snapshot
(`tests/contract/test_openapi_snapshot.py`, regen with `HAPPYRANCH_REGEN_OPENAPI=1`)
**AND** the web `openapi-coverage.test.ts` (every browser-callable route needs a TS
mirror in `web/src/lib/api/`, MEM-354). Build must regenerate both **in the same PR**,
or the contract tests go red / the coverage test is a false-green.

### 7c. Spawn-time materialization seam (the load-bearing bit)

At **session spawn**, for the task being spawned:

1. **Resolve** the task's (inherited, §8) attachment set (own + ancestors union).
2. **Materialize**: for each attachment, read bytes from the task-attachment private
   store and write the file into a **new per-session attachment dir** the executor
   can `Read`. Target:
   `<workspace>/.happyranch/attachments/<session_id>/<storage_key>__<sanitized_name>`.
   The filename uses a **deterministic collision-safe pattern**
   (`{storage_key}__{sanitized_display_name}`) so that own and ancestor attachments
   with the same display_name do not overwrite each other. Display names are
   sanitized at the materialization boundary to prevent malformed DB names from
   escaping the session directory. The prompt `Attachments:` block still shows the
   original human-readable display_name.
3. **Inject** an `Attachments:` block into the brief prompt (in `_build_prompt`,
   alongside the `brief: {brief}` line, ~L389), naming each file + its materialized
   absolute path + size + content-type hint:

   ```text
   Attachments (materialized to disk — load them by path with your own tools; how much
   you extract from an image depends on this CLI's abilities):
   - mockup.png (image/png, 124033 bytes) -> /…/.happyranch/attachments/sess-XXX/key__mockup.png
   ```

**This CHANGES what is materialized into a worker session at spawn.** Therefore the
build **must** add a doc-parity paragraph to `protocol/05b-agent-runtime.md` (session
spawn / materialization) in the **same PR**. **This spec does not edit protocol/** —
the delta is specified here only:

> **Proposed protocol/05b delta (land at build time, not now):** a "Task attachment
> materialization at session spawn" paragraph stating that when a task (or an ancestor
> it inherits from) has attachments, the runtime resolves them up the `parent_task_id`
> chain, reads durable bytes from the task-attachment private store, writes them into
> the per-task session attachment dir, and injects an `Attachments:` block into the
> brief prompt; that delivery is by-path for all executors and image perception is
> executor-dependent; and that the dir is cleaned per §9.

### 7d. Read on the task surface

The founder (and agents) can view/download a task's attachments on the task-detail
view (web §12) and via CLI (`happyranch tasks show <id>` prints attachment lines).
Download/read APIs use the existing authenticated org-scoped bearer model (seq25):
any authenticated caller in the same org may list/download task attachments for an
extant task. Inherited entries (own + ancestors) are resolved via the
`parent_task_id` chain. Cross-org access is denied by the existing org-scoped
context. Private task-brief bytes remain isolated from the org-wide artifact store
(see §4).

## 8. Task-tree inheritance — RECOMMENDED: resolve-UP the tree at materialization

Attachments are **keyed to the task the founder attached to** (the owning ancestor).
Every **descendant** task must resolve them. Recommendation: **resolve UP the
`parent_task_id` chain at materialization time** and treat the nearest ancestor(s)
carrying attachments as the single source of truth — **no copying per child**.

Concretely, at spawn (§7c step 1): walk `parent_task_id` from the spawning task up to
the root, unioning any `task_attachments` rows found (or: stop at the first ancestor
that has them — see the open sub-question below), and materialize that set.

**Why resolve-up, not copy-on-spawn:**

- **Single source of truth.** `tasks.parent_task_id` already exists
  (`database.py` ~L459-474) and already encodes the tree — resolve-up needs **no new
  linkage** and **no write on spawn**. Copy-on-spawn would duplicate `task_attachments`
  rows into every child, multiplying rows and risking divergence if the founder later
  edits/removes an attachment on the owning task.
- **Cheap and consistent.** The blob is stored once in the task-attachment private
  store regardless; resolve-up just recomputes the *reference set* per spawn. A founder
  removing an attachment on the owning task is instantly reflected in not-yet-spawned
  descendants.
- **Matches the founder ruling directly (seq3):** "the dev the EM hands it to must
  also see it" — the dev's task is a descendant; resolve-up reaches the ancestor's
  attachments without the founder having to re-attach.

**Open sub-question (§13):** union-all-ancestors vs. nearest-ancestor-only. v1
recommendation: **union up to the root** (a founder attaching at any level is
inherited downward), with the founder-owning task being the usual sole source. Flag
if the founder wants nearest-only semantics.

## 9. Constraints to enumerate (v1 acceptance surface)

1. **File-type allowlist.** v1 recommendation: allow images
   (`image/png|jpeg|gif|webp`) + common docs (`pdf`, `csv`, `txt`, `md`) — the
   founder's stated use cases. Reject executables/archives by default. (Founder to
   confirm the list, §13.)
2. **Size caps.** Mirror the current artifact-store default unless founder chooses
   otherwise: **per-file 10 MB** (`MAX_ARTIFACT_BYTES`, `artifact_store.py:20`) — do
   **not** raise it here. Add a **per-task cap** (recommend ≤ 5 attachments per task,
   mirroring the thread `too_many_attachments` = 5 rule) and optionally a per-task
   aggregate byte cap.
3. **Retention / cleanup — durable metadata.** `task_attachments` rows + private-store
   blobs persist with the task (auditability); recommend **cleanup when a task tree
   reaches a terminal state** is a **follow-up**, not v1. v1: no automatic durable blob
   deletion; document it.
4. **Cleanup — materialized per-session dir.** The materialized files in the session
   attachment dir are a **regenerable cache** (bytes of record live in the
   task-attachment private store, MEM-210 read/write-asymmetry lens). Recommend the dir
   is **cleaned on session end** (or overwritten fresh on next spawn), so
   stale/oversized copies don't
   accumulate under the agent workspace. This cleanup is v1 (it's the only new
   filesystem footprint per session).
5. **Validation errors** (mirror the thread rules): empty storage ref →
   `404 task_attachment_not_found`; duplicate `storage_key` on one task →
   `422 duplicate_attachment`; > cap → `422 too_many_attachments`; disallowed
   content-type → `422 unsupported_attachment_type`; invalid display name (must be
   non-empty, ≤ 200 chars, no `/ \` or control chars) → `422
   invalid_attachment_display_name`.
6. **Materialization collision safety.** If the spawning task and an ancestor have
   attachments with the same display_name, the materialized filenames must be
   deterministic and collision-safe. Use the pattern
   ``{storage_key}__{sanitized_display_name}`` so the globally-unique storage_key
   prevents overwrites. Sanitize display_name at the materialization boundary so
   malformed DB names cannot escape the session attachments directory.
7. **Storage-key path containment.** `storage_key` is validated as a safe-token
   (regex `^[A-Za-z0-9._@+-]+$`, no `..`, `/`, `\`, null bytes). Store lookups
   use `path_for()` which resolves against the task-attachment root with an in-root
   containment check. The same containment check guards materialized filenames.

## 10. Auth / permission decisions (settled)

Founder ruling seq14/15 settles storage: task-brief attachments need a dedicated
private store/root.

Founder ruling seq25 settles BROWSER/CLI read access: any authenticated caller in
the same org may list/download task attachments from the dedicated task-attachment
store. This loosens the prior task-tree-only read restriction for the list/download
routes. No requester task/session identity is accepted or required; cross-org access
remains denied by the existing org-scoped context.

Spawn-time materialization remains task-tree scoped (own + ancestors via
`parent_task_id`), as does upload/reference.

Closed boundaries (no auth/permission-model change needed):

- **Web auth:** the task-attachment upload/list/download routes use the existing
  daemon bearer-token flow unchanged. No new auth surface.
- **Agent CLI permissions:** agent-originated uploads use the existing baseline
  `happyranch` CLI allowance without modifying Claude `--allowedTools`, Codex sandbox
  flags, opencode `permission.bash`, or the generated allow-rule surface.
- **Host-path boundary:** explicit upload of a user/agent-selected file is done
  through the task-attachment upload path; no broad arbitrary host-path reads are
  granted.

## 11. Audit actions (scope-prefix convention preserved)

New additive audit actions mirroring the artifact/thread families:
`task_attachment_uploaded` (new private-store upload), `task_attachment_added` (on
`POST /tasks` with attachments, and on the owning task),
`task_attachment_materialized` (per session spawn that resolves ≥1 attachment; carries
the resolved set + session dir), and — if cleanup lands — `task_attachment_cleaned`.

**Convention preserved, not overloaded:** these rows set `task_id=<TASK-NNN>` — the
ordinary primary use of the column, **not** the `audit_log.task_id` scope-prefix
overload (MEM-075). The new `task_attachment_uploaded` action records private-store
uploads; no scope-prefix semantics change.

## 12. Web UI (FE-only slice)

- **Task-create form:** an upload widget mirroring the thread composer — select/drop up
  to the per-task cap, show name/size/upload-state/remove, upload each file through the
  task-attachment upload route before submit, then send the `attachments` refs on
  `POST /tasks`.
  Disable submit while uploads are in flight; on upload failure block submit and mark
  the file; on submit failure after upload keep refs for retry (thread-composer
  behavior, verbatim).
- **Task-detail view:** surface the task's (inherited) attachments as chips/compact
  cards with a download action calling the task-attachment download route —
  read/download only, org-scoped bearer (seq25) — any authenticated org member may
  list/download; inherited attachments are resolved own + ancestors.
- **Design-token compliant**, reusing existing components (no bespoke calendar/complex
  UI). This is a straightforward FE slice.
- Adding the daemon route(s) drifts OpenAPI + `openapi-coverage.test.ts` — regenerate
  both in the same PR (§7b, MEM-094/148/354).

## 13. Open founder sign-off items (the gate)

Build is blocked until the founder signs off on this design. Specifically:

1. **Ratify v1 scope: BOTH founder AND agents attach (agent-attach IN), guarded to
   task-attachment private-store references only.** Confirm that agents may attach at
   task-create/dispatch time via the **new task-attachment API/CLI upload path**
   (delegation-inherit + agent-origination, §1), and that the **retained invariant**
   holds: attach = explicitly upload bytes into the task-attachment private store, then
   reference the resulting task-attachment key, **NOT** a new capability to read
   arbitrary absolute host filesystem paths — no widening of host-disk reach; any step
   needing a new local-path-read capability or auth/permission-model change STOPs and
   escalates. §1 / §14.
2. **Confirm the inheritance model:** resolve-**UP** the `parent_task_id` tree at
   materialization (single source of truth, no copy-on-spawn) — §8 — and the
   union-to-root vs. nearest-ancestor sub-question (§8 recommends union-to-root).
3. **Confirm the auth/permission boundaries (§10):** the new task-attachment upload
   route/CLI path must not require auth / bearer-token / permission-generation changes
   unless the founder explicitly approves that expansion. The build must STOP and
   escalate rather than assume a new permission surface.
4. **Set the concrete constraint numbers (§9):** the **file-type allowlist**, the
   **per-task attachment count cap** (recommend 5), whether to add a per-task aggregate
   byte cap, and confirm **per-file 10 MB** (mirrored current cap) is retained
   unchanged.
5. **Acknowledge the doc-parity deltas land in the build PR:** the
   `protocol/05b-agent-runtime.md` session-spawn materialization paragraph (§7c) **and**
   the OpenAPI snapshot + web `openapi-coverage.test.ts` regen (§7b/§12) are all part of
   the same build PR.

*(Note: §5 — where the daemon's job ends and how much a worker extracts from an image —
is a design boundary inherent to the worker CLI, **not** a sign-off decision; it was
removed from this gate.)*

On sign-off, the build lands as a phased engineering effort (new `task_attachments`
table + dedicated private task-attachment store/root + new task-attachment upload
API/CLI path + `POST /tasks` `attachments` ref + task read view + spawn-time
resolve-up/materialize/inject seam + per-session-dir cleanup + web upload/detail slice
+ CLI `tasks show` + audit + **protocol/05b doc-parity in the same PR**), routed
through the normal dev → code_reviewer → qa merge gate. **No part of it is authorized
to build before sign-off**, and any implementation step that appears to require
touching an existing schema column, the `audit_log` scope convention, **auth / the
bearer-token flow**, a permission-generation surface, or a **new local-path-read
capability** (arbitrary absolute host paths, beyond the explicit task-attachment upload
path — §1 invariant) must STOP and escalate (§4 / §10 boundary).

## 14. Non-goals (v1 no-list, consolidated)

- No **new local-path-read capability** — *attach* means "explicitly upload bytes into
  the task-attachment private store, then reference the resulting task-attachment key",
  for both founder and agents. Agents do **not** gain the ability to reference
  arbitrary absolute host filesystem paths; host-disk reach is **not** widened. If a
  step needs that, STOP and escalate — §1/§13 invariant. *(Agent-attach itself is IN
  v1 — §1; only the arbitrary-host-path reach is excluded.)*
- No use of the **org-wide shared artifact store as durable backing** for private
  task-brief attachments. The existing artifact store remains for shared cross-task
  artifacts; task-brief bytes live once in the new task-attachment private store and
  the task table stores references + metadata only.
- No **inline multipart on `POST /tasks`** — reference-only route, client does
  task-attachment upload-then-reference (§7b).
- No **copy-on-spawn** of attachment rows into children — resolve-up the tree instead
  (§8).
- No **auth / bearer-token / permission-generation change** without founder approval —
  if the transport needs one, STOP and escalate (§10). Additive table + additive audit
  actions + new private task-attachment store only.
- No **per-executor image special-casing** — by-path delivery for all; how much a
  worker extracts from a file is inherent to its own CLI, not a daemon knob (§5).
- No **automatic private-store blob deletion** on task-tree completion in v1
  (follow-up); only the regenerable per-session materialized dir is cleaned (§9).
- No **file previews / virus scanning / content extraction** (matches the thread-spec
  non-goals).
