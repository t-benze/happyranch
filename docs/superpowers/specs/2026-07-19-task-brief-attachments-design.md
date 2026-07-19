# Task-Brief File Attachments — Design Spike

**Date:** 2026-07-19
**Status:** DESIGN-ONLY. **Build is gated on founder sign-off of THIS design.** No
implementation, no schema migration, no daemon route, and no protocol/ edit may
land until the founder approves the design below — see §12 (Open founder sign-off
items).
**Origin:** THR-109. Founder stated brief-as-pure-text is insufficient and asked to
attach files — especially images (mockups) — when creating a task. Founder ruling
(seq3): a founder-attached file must be reachable by the **assigned agent AND every
sub-agent it delegates to down the task tree** (she attaches a mockup to a task that
lands on the EM; the dev the EM hands it to must also see it).
**Author:** engineering_manager (design spike; founder is the reviewer/approver).
**Relates to:**
- `runtime/orchestrator/orchestrator.py` — the brief → prompt build (`brief: {brief}`
  line, ~L389) and the per-session executor cwd (`workspaces_dir / agent_name`,
  ~L526). The materialization seam (§7) hooks here.
- `runtime/orchestrator/executors.py` — `ClaudeExecutor` passes the full prompt via
  the CLI `-p` arg (~L663-679). An image cannot ride this text channel (§3).
- `runtime/infrastructure/artifact_store.py` — the org-shared, file-based blob store
  (`MAX_ARTIFACT_BYTES = 10 MB`, `artifact_store.py:20`) reused for the bytes (§6).
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
  here in the same PR (§7c / §12). **Not edited now** — the delta is specified, not
  applied.

---

## 1. Goal

Let the founder **attach files — especially images (mockups) — when creating a
task**, and guarantee those files are readable by the assigned agent **and by every
descendant task** the agent spawns down the tree, delivered as real files the
executor can `Read` plus an `Attachments:` block naming each file and its on-disk
path in the brief prompt.

The capability is intentionally **narrow for v1**: only the **founder** attaches, and
only **at task-create time**. An **agent** attaching arbitrary files off its own disk
is **out of scope** (it brushes the permission model) and is noted as a founder-gated
fast-follow only (§13).

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
artifact store → every task in the tree resolves the same attachment set at spawn →
files are materialized to a per-session dir + named in the brief prompt.*

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

## 4. Architecture: a task-scoped sibling of thread attachments

Task attachments are the **task-tree analogue** of the already-built thread
attachments. The design **reuses the thread-attachment machinery as its template and
its byte store**, and adds exactly one additive table + one materialization seam:

| | **Thread attachments** (built) | **Task attachments** (this spec) |
|---|---|---|
| Keyed to | a thread message / a thread | **a task** (`task_id`) |
| Byte store | org artifact store | **same** org artifact store |
| Metadata table | `thread_message_attachments` / `thread_scoped_attachments` | **new `task_attachments`** |
| Reaches | thread participants | the task **and every descendant** (resolve-up, §8) |
| Delivered as | prompt `Attachments:` block + `artifacts get` | **materialized files** + prompt `Attachments:` block (§7c) |

**Additive-only invariant.** This design **does not alter or drop** any existing
column and **does not overload** any existing semantics — not `audit_log.task_id`
scope-prefixes, not `tasks.blocked_on_job_ids`, not the `brief` column. It gets its
**own** additive table, reuses the artifact store **unmodified**, and adds **additive**
audit actions (§11).

> **Boundary / STOP-and-escalate.** If, during build, the design appears to require
> altering an existing schema column, overloading `audit_log.task_id` scope
> semantics, touching **authentication / the daemon bearer-token flow**, or touching
> a **permission-generation surface** (Claude `--allowedTools`, Codex sandbox flags,
> opencode `permission.bash`, the baseline `happyranch` allow-rule), the implementer
> **must STOP and escalate**. Those are founder-contract surfaces outside EM
> authority. See §10 for the one genuinely-open transport question that touches the
> bearer-token multipart path.

## 5. Executor capability matrix — honest degradation

Files are delivered **by path, universally**: the materialization step (§7c) writes
real files into a session dir and the `Attachments:` block names each path. Every
executor can therefore *reach* the bytes.

**Image perception is best-effort per executor** and is **not** guaranteed by this
design:

| Executor | File reachable by path | Native image perception |
|---|---|---|
| Claude Code | yes | **yes** — can `Read` an image file and see it |
| Codex | yes | **varies** — may only see the path/bytes, not perceive the image |
| opencode | yes | **varies** |

**No per-executor special-casing.** The runtime delivers the same artifact +
`Attachments:` block to all executors. The honest statement in the brief block is:
"these files are on disk at these paths; image perception depends on your executor."
A founder attaching a mockup for a *frontend_engineer* (Claude-based) gets perception;
the same mockup handed to a Codex-based reviewer is reachable but may not be *seen*.
This is a known, documented degradation, not a bug to paper over.

## 6. Data model — a new `task_attachments` table

A dedicated additive table, shaped after `thread_message_attachments`
(`database.py` ~L665) so it inherits the same artifact-ref / metadata-snapshot
conventions without touching any existing table. **Illustrative** (final DDL is an
implementation detail, gated on sign-off):

```sql
CREATE TABLE IF NOT EXISTS task_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,          -- the task the founder attached to (owning ancestor)
    ordinal INTEGER NOT NULL,       -- display / injection order
    artifact_name TEXT NOT NULL,    -- download key into the org artifact store
    display_name TEXT NOT NULL,     -- UI label / materialized filename basename
    size_bytes INTEGER,             -- metadata snapshot
    content_type TEXT,              -- metadata snapshot (drives image-vs-file hinting)
    uploaded_by TEXT NOT NULL,      -- 'founder' in v1
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(task_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id);
```

- **Bytes are NOT duplicated.** `artifact_name` is the sole download key into the
  existing artifact store (`artifact_store.py`); the table stores metadata only,
  exactly like the thread-attachment tables. `size_bytes` / `content_type` are
  display/durability snapshots.
- **`tasks` is untouched.** No new column on `tasks`; `brief TEXT NOT NULL` and
  `parent_task_id` are unchanged. `TaskRecord` (`models.py` ~L71-81) gains a
  **read-only** `attachments: list[TaskAttachmentRef]` view populated from the join,
  not a stored column.
- **Additive only.** New table + new read view + additive audit actions (§11). No
  existing column altered or re-meant; no overloaded-column semantics changed. (An
  additive table is within the migration guardrails; a column drop/alter or an
  overloaded-column re-meaning would be founder-gated — none is proposed.)

## 7. Lifecycle

### 7a. Reference-only daemon contract (mirrors the thread template)

Per the thread-attachment ruling, **daemon task routes stay reference-only**: bytes go
to the artifact store first, then the task references artifact names. This keeps one
source of truth for bytes, reuses artifact validation/caps/audit, and avoids a second
byte store. The **client** (web/CLI) provides the ergonomic upload-then-reference
workflow.

### 7b. Upload surface — RECOMMENDED: multipart-upload-then-reference

**Recommendation: reuse the thread-attachment two-step — upload to the artifact route,
then reference on `POST /tasks`** — rather than inline multipart on `POST /tasks`.

Rationale (same as the thread spec's "Chosen Approach"): a reference-only
`POST /tasks` keeps the task-create contract simple, reuses the existing artifact
upload route's cap/name/audit/atomic-write enforcement, and avoids a second multipart
handler that would duplicate that logic and complicate OpenAPI + web-client mirroring.

Concretely:
1. Web/CLI uploads each local file to the existing artifact route
   (`POST /api/v1/orgs/{slug}/artifacts`), getting back collision-resistant
   `artifact_name`s (v1 scheme e.g. `task-<id-or-draft>-<UTC ts>-<sanitized basename>`).
2. **`POST /tasks` body gains an optional `attachments: [{artifact_name,
   display_name?}]`** array. The daemon validates each referenced artifact exists,
   then writes `task_attachments` rows for the new task.
3. Read surface: **`GET /tasks/{id}`** (or the task-detail route) returns the
   `attachments` view for display/download on the task-detail page.

**Contract-drift call-out (MEM-094 / MEM-148):** adding/extending these daemon routes
drifts **BOTH** contract surfaces — the Python OpenAPI snapshot
(`tests/contract/test_openapi_snapshot.py`, regen with `HAPPYRANCH_REGEN_OPENAPI=1`)
**AND** the web `openapi-coverage.test.ts` (every browser-callable route needs a TS
mirror in `web/src/lib/api/`, MEM-354). Build must regenerate both **in the same PR**,
or the contract tests go red / the coverage test is a false-green.

**Open decision (§10):** whether the web founder-upload reuses the existing
bearer-token multipart artifact-upload path unchanged, or needs any auth change. If it
needs an auth/bearer-token change, that is **founder-gated and out of scope** — STOP.

### 7c. Spawn-time materialization seam (the load-bearing bit)

At **session spawn**, for the task being spawned:

1. **Resolve** the task's (inherited, §8) attachment set.
2. **Materialize**: for each attachment, `artifacts get` the bytes and write the file
   into a **new per-task/session attachment dir** the executor can `Read`. A per-task
   dir **must be introduced** — cwd today is the shared `workspaces_dir/<agent_name>`
   (~L526), which is wrong for task-scoped files. Proposed target (illustrative):
   `workspaces_dir/<agent_name>/.happyranch/attachments/<task_id>/<display_name>` (a
   dot-dir the agent won't confuse with work product), or a session-scoped temp dir
   cleaned on session end (§9 cleanup). Final path is an implementation detail gated on
   sign-off.
3. **Inject** an `Attachments:` block into the brief prompt (in `_build_prompt`,
   alongside the `brief: {brief}` line, ~L389), naming each file + its materialized
   absolute path + size + content-type hint, mirroring the thread-prompt format:

   ```text
   Attachments (materialized to disk — Read them by path; image perception is
   best-effort per executor):
   - mockup.png (image/png, 124033 bytes) -> /…/.happyranch/attachments/TASK-XXX/mockup.png
   ```

**This CHANGES what is materialized into a worker session at spawn.** Therefore the
build **must** add a doc-parity paragraph to `protocol/05b-agent-runtime.md` (session
spawn / materialization) in the **same PR**. **This spec does not edit protocol/** —
the delta is specified here only:

> **Proposed protocol/05b delta (land at build time, not now):** a "Task attachment
> materialization at session spawn" paragraph stating that when a task (or an ancestor
> it inherits from) has attachments, the runtime resolves them up the `parent_task_id`
> chain, writes them into the per-task session attachment dir, and injects an
> `Attachments:` block into the brief prompt; that delivery is by-path for all
> executors and image perception is executor-dependent; and that the dir is cleaned per
> §9.

### 7d. Read on the task surface

The founder (and agents) can view/download a task's attachments on the task-detail
view (web §7-UI) and via CLI (`happyranch tasks show <id>` prints attachment lines,
mirroring `threads show`).

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
- **Cheap and consistent.** The blob is stored once in the artifact store regardless;
  resolve-up just recomputes the *reference set* per spawn. A founder removing an
  attachment on the owning task is instantly reflected in not-yet-spawned descendants.
- **Matches the founder ruling directly (seq3):** "the dev the EM hands it to must
  also see it" — the dev's task is a descendant; resolve-up reaches the ancestor's
  attachments without the founder having to re-attach.

**Open sub-question (§12):** union-all-ancestors vs. nearest-ancestor-only. v1
recommendation: **union up to the root** (a founder attaching at any level is
inherited downward), with the founder-owning task being the usual sole source. Flag
if the founder wants nearest-only semantics.

## 9. Constraints to enumerate (v1 acceptance surface)

1. **File-type allowlist.** v1 recommendation: allow images
   (`image/png|jpeg|gif|webp`) + common docs (`pdf`, `csv`, `txt`, `md`) — the
   founder's stated use cases. Reject executables/archives by default. (Founder to
   confirm the list, §12.)
2. **Size caps.** Align to the artifact store: **per-file 10 MB**
   (`MAX_ARTIFACT_BYTES`, `artifact_store.py:20`) — do **not** raise it here. Add a
   **per-task cap** (recommend ≤ 5 attachments per task, mirroring the thread
   `too_many_attachments` = 5 rule) and optionally a per-task aggregate byte cap.
3. **Retention / cleanup — durable metadata.** `task_attachments` rows + artifact
   blobs persist with the task (auditability); recommend **cleanup when a task tree
   reaches a terminal state** is a **follow-up**, not v1 (the thread spec explicitly
   made artifact cleanup a non-goal). v1: no automatic blob deletion; document it.
4. **Cleanup — materialized per-session dir.** The materialized files in the session
   attachment dir are a **regenerable cache** (bytes of record live in the artifact
   store, MEM-210 read/write-asymmetry lens). Recommend the dir is **cleaned on
   session end** (or overwritten fresh on next spawn), so stale/oversized copies don't
   accumulate under the agent workspace. This cleanup is v1 (it's the only new
   filesystem footprint per session).
5. **Validation errors** (mirror the thread rules): empty artifact ref →
   `404 artifact_not_found`; duplicate `artifact_name` on one task →
   `422 duplicate_attachment`; > cap → `422 too_many_attachments`; disallowed
   content-type → `422 unsupported_attachment_type`; invalid display name (must be
   non-empty, ≤ 200 chars, no `/ \` or control chars) → `422
   invalid_attachment_display_name`.

## 10. Open transport / auth decision (surfaced, not guessed)

**The one genuine unknown:** does the web founder-upload reuse the **existing
bearer-token multipart artifact-upload path unchanged**, or does task-create-time
upload need any change to auth / the bearer-token flow?

- **Assumption (stated before making it):** the web app already uploads artifacts via
  the bearer-authed `POST /artifacts` route for thread compose `--attach`; the
  founder task-create upload should reuse **that exact path unchanged**, so **no auth
  change is needed**. Under this assumption the whole feature stays additive and inside
  EM authority.
- **If that assumption is false** — i.e. task-create upload cannot reuse the existing
  bearer multipart path and would require a new/changed auth or bearer-token surface —
  then per my authority limits this is **founder-gated: STOP and escalate**, do not
  build. This is called out as a hard boundary rather than guessed past.

## 11. Audit actions (scope-prefix convention preserved)

New additive audit actions mirroring the artifact/thread families:
`task_attachment_added` (on `POST /tasks` with attachments, and on the owning task),
`task_attachment_materialized` (per session spawn that resolves ≥1 attachment; carries
the resolved set + session dir), and — if cleanup lands — `task_attachment_cleaned`.

**Convention preserved, not overloaded:** these rows set `task_id=<TASK-NNN>` — the
ordinary primary use of the column, **not** the `audit_log.task_id` scope-prefix
overload (MEM-075). Existing `artifact_put` rows already record the uploaded bytes. No
scope-prefix semantics change.

## 12. Web UI (FE-only slice)

- **Task-create form:** an upload widget mirroring the thread composer — select/drop up
  to the per-task cap, show name/size/upload-state/remove, upload each file to
  `/artifacts` before submit, then send the `attachments` refs on `POST /tasks`.
  Disable submit while uploads are in flight; on upload failure block submit and mark
  the file; on submit failure after upload keep refs for retry (thread-composer
  behavior, verbatim).
- **Task-detail view:** surface the task's (inherited) attachments as chips/compact
  cards with a download action calling the artifact download route — read/download
  only.
- **Design-token compliant**, reusing existing components (no bespoke calendar/complex
  UI). This is a straightforward FE slice.
- Adding the daemon route(s) drifts OpenAPI + `openapi-coverage.test.ts` — regenerate
  both in the same PR (§7b, MEM-094/148/354).

## 13. Open founder sign-off items (the gate)

Build is blocked until the founder signs off on this design. Specifically:

1. **Ratify v1 scope: founder-attaches-at-create-time only.** Confirm that
   **agent-attaches-arbitrary-files-off-its-own-disk is a founder-gated fast-follow**,
   OUT of v1 (it brushes the permission model — an agent writing arbitrary local bytes
   into the org store as a "task attachment" is a new capability surface). §1.
2. **Confirm the inheritance model:** resolve-**UP** the `parent_task_id` tree at
   materialization (single source of truth, no copy-on-spawn) — §8 — and the
   union-to-root vs. nearest-ancestor sub-question (§8 recommends union-to-root).
3. **Confirm the transport/auth assumption (§10):** web founder-upload reuses the
   existing bearer-token multipart artifact path **unchanged**. If it would require any
   auth / bearer-token / permission-model change, that is founder-gated and re-scopes
   the feature — the build must STOP and escalate rather than proceed.
4. **Set the concrete constraint numbers (§9):** the **file-type allowlist**, the
   **per-task attachment count cap** (recommend 5), whether to add a per-task aggregate
   byte cap, and confirm **per-file 10 MB** (artifact-store cap) is retained
   unchanged.
5. **Accept the honest executor degradation (§5):** files are delivered by-path
   universally; **image perception is best-effort** (Claude yes; Codex/opencode vary),
   with no per-executor special-casing.
6. **Acknowledge the protocol/05b doc-parity delta (§7c)** lands in the build PR, and
   the OpenAPI + web-coverage regen (§7b/§12) is part of the same PR.

On sign-off, the build lands as a phased engineering effort (new `task_attachments`
table + `POST /tasks` `attachments` ref + task read view + spawn-time
resolve-up/materialize/inject seam + per-session-dir cleanup + web upload/detail slice
+ CLI `tasks show` + audit + **protocol/05b doc-parity in the same PR**), routed
through the normal dev → code_reviewer → qa merge gate. **No part of it is authorized
to build before sign-off**, and any implementation step that appears to require
touching an existing schema column, the `audit_log` scope convention, **auth / the
bearer-token flow**, or a permission-generation surface must STOP and escalate (§4 /
§10 boundary).

## 14. Non-goals (v1 no-list, consolidated)

- No **agent-attaches-arbitrary-local-files** (founder-gated fast-follow; brushes the
  permission model) — §1/§13.
- No **second byte store** — bytes live once in the org artifact store; the task table
  stores references + metadata only.
- No **inline multipart on `POST /tasks`** — reference-only route, client does
  upload-then-reference (§7b).
- No **copy-on-spawn** of attachment rows into children — resolve-up the tree instead
  (§8).
- No **auth / bearer-token / permission-generation change** — if the transport needs
  one, STOP and escalate (§10). Additive table + additive audit actions + reused
  artifact store only.
- No **per-executor image special-casing** — by-path delivery for all; perception is
  best-effort (§5).
- No **automatic artifact-blob deletion** on task-tree completion in v1 (follow-up);
  only the regenerable per-session materialized dir is cleaned (§9).
- No **file previews / virus scanning / content extraction** (matches the thread-spec
  non-goals).
