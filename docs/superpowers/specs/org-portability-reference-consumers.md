# Org Portability — DB-to-Filesystem Reference Consumers (THR-187 Step 0 Evidence Gate)

> Status: current (design reference for THR-187 Step 0 only)
> Current Source: this document; executable truth in `runtime/` at head `9028700f9caeb5cb507e3600cd279dccd064000a`
> Scope: evidence gate only — consumer map, fixture registry, compatibility matrix, source-sidecar harness design
> Supersedes: none (first artifact for the Step 0 gate)

This document is the **Step 0 Evidence Gate** for THR-187 offline organization
migration. It records *verified fact* about the current immutable implementation
head, and separates it from *proposed future seams* that later slices will
implement. It is **not** an implementation plan for archive/export/import.

---

## 0. Purpose, scope, and no-go

### Purpose

Before any archive/export/import code is written, the design (TASK-5426 §6)
requires a complete, implementation-auditable enumeration of every place where
**database-held data resolves to filesystem bytes**. This document is that
artifact. It answers, per consumer:

- which table + column(s) hold the reference and what shape they store;
- which resolver function/class and which production call sites resolve it;
- what root/path-construction rule applies and what traversal/symlink
  containment exists;
- what validation/integrity rule is enforced;
- how the consumer behaves when the file is missing or invalid;
- what the **portable disposition** is (include / exclude / reject /
  target-local) and why; and
- which fixture(s) exercise it.

### Scope

- **Step 0 only.** No archive, export, import, staging, publication, admission
  maps, marker handling, schedules, Slice-C activation, routes, CLI behavior,
  schema/migrations, auth/credentials, or permission-model changes are
  authorized by this gate.
- **PR #684 is excluded.** It is not resumed, inspected-for-reuse,
  cherry-picked, merged, altered, or commented on. Only line-level *utility*
  ideas may later be newly extracted from it *after* this evidence gate earns
  independent review; all HTTP fences, online locks/claims, retry convergence,
  receipt protocol, and route assumptions remain excluded.

### No-go (fail-closed boundary)

- **Current-v2 same-slug relocation into an absent destination slug** is the
  only product boundary. It is not a clone, source deletion, merge/overwrite,
  credential transfer, online fence, retry/receipt protocol, automatic rebind,
  or automatic rearm.
- No v0/v1 implicit conversion. Every non-current-v2 case is a **named
  refusal**, not auto-upgrade, conversion, or best effort (see §6).
- No alteration of schema or of any overloaded-column meaning.
- The classifier-not-`ALLOWED_ROOTS` policy (§8) and the single target-only
  inactive-marker contract (§8) are preserved from TASK-5426; their later
  slices are **not** designed here beyond relevance to consumer disposition.

### STOP conditions

Any of the following discovered during tracing is a STOP — the disposition is
not invented; the task reports blocked/escalation (§9):

- an **unclassified** DB→file consumer (a column that resolves to filesystem
  bytes with no resolver found in the map below);
- a resolution-bearing **absolute source path** outside the org root, or a
  path escaping the allowable root;
- a required file outside the approved classifier policy;
- any schema/migration, overloaded-column, auth/credential, or
  permission-model change requirement.

---

## 1. Verified implementation head and impact radius

All file:symbol references and line numbers below were verified against the
immutable main head at the time of this gate:

```
9028700f9caeb5cb507e3600cd279dccd064000a
docs(thread): require real newlines/Markdown in reply body, forbid literal \n (THR-197) (#692)
```

The org SQLite schema is created in `runtime/infrastructure/database.py`
`Database._create_tables()` (lines ~663–1160). Per-org root paths are derived
from `OrgPaths` (`runtime/orchestrator/_paths.py`) whose `root` is
`<runtime>/orgs/<slug>`; the org-level stores are constructed in
`runtime/daemon/org_state.py` (e.g. `self.thread_store = ThreadStore(self.root / "threads")`
at line 108). The classifier that governs portable roots is
`runtime/portability/roots.py` (`classify_root_entries` / `_classify_child`).

**Native impact evidence (this gate):** this PR adds exactly one new file under
`docs/superpowers/specs/`. It changes no production code, no test code, and no
runtime data. There are no importers, callers, or config consumers of a spec
document; the only "consumer" is the human review/QA gate and future Slice B/C
implementers who read it. Risk tier is HIGH only in the sense that it is
durable-data *design evidence* — it carries no executable risk itself.

Evidence commands and results are summarized inline per consumer; the raw
commands were `rg` over `runtime/` and `sqlite3 -readonly` over a live org DB
(read-only) to confirm stored shapes.

---

## 2. DB-to-filesystem consumer map

Consumers are traced from the **producer table → stored column → resolver →
path-construction → validation → recoverability → disposition**. A "consumer"
here means a column whose value is resolved (at read time, or at the sole
write site) to filesystem bytes under the org root.

### Legend for disposition

| Disposition | Meaning |
| --- | --- |
| `include` | The files the column points at are portable bytes carried in the archive. |
| `exclude` | The files are machine-bound/regenerable and must not be carried. |
| `reject` | Presence of this shape is a refusal (unknown/unsafe). |
| `target-local` | The file is portable, but the DB *value* is machine-specific and must be re-resolved on the destination. |

### C1 — `task_attachments.storage_key` → `TaskAttachmentStore`

- **Producer table/field/shape:** `task_attachments.storage_key` (TEXT, UNIQUE);
  a flat safe token (not a nested key). `database.py` lines ~954–969.
- **Resolver:** `runtime/infrastructure/task_attachment_store.py`
  `TaskAttachmentStore.path_for(storage_key)` → `self._root / storage_key`.
  Validated first by `validate_storage_key` (regex `^[A-Za-z0-9._@+-]+$`,
  max 256 chars, rejects `..`, `/`, `\`, NUL).
- **Direct production call sites:**
  - upload/download: `runtime/daemon/routes/tasks.py:1583`
    (`TaskAttachmentStore(OrgPaths(org.root).task_attachments_dir)`);
  - decision-attachment validation: `validate_task_attachment_refs`
    (`task_attachment_store.py`) called from orchestrator
    delegate/chain/fanout (`run_step.py:894,943,1727,2778`);
  - **materialization read:** `Orchestrator._materialize_task_attachments`
    (`orchestrator.py:426`) constructs `TaskAttachmentStore` at `:473` and
    reads each persisted `storage_key` via `store.read(att.storage_key)` at
    `:484`.
- **Root/path rule:** `<org>/task-attachments/<storage_key>` via
  `OrgPaths.task_attachments_dir`.
- **Traversal/symlink containment (actual current behavior):**
  `validate_storage_key` rejects separators and `..` at the string level.
  `path_for` then resolves the target and asserts the **resolved** target stays
  inside the store root (`task_attachment_store.py:177,184–199`). This rejects
  a symlink whose resolved target escapes the root, but it does **not** reject
  symlinks as such: an **in-root symlink** (a link whose target is another file
  inside `task-attachments/`) passes containment, and `read()` subsequently
  follows it (`:224–230`). The org-root classifier (`roots.py:122–155`)
  inspects only direct org children, so a nested symlink/nonregular member is
  not caught there either.
- **Validation/integrity (required future portability validation — not present
  today):** capture/import must reject **every** symlink and every nonregular
  (FIFO/socket/device) member at any depth under `task-attachments/`, fail
  closed (`nonregular`), before any read; the token-shape + in-root containment
  check is insufficient for that boundary. The runtime integrity rules (token
  shape + existence on disk + "not already claimed" DB lookup at claim time)
  remain unchanged.
- **Recoverability:** missing file → `TaskAttachmentNotFound` / structured
  `task_attachment_not_found`; invalid key → `TaskAttachmentInvalidStorageKey`.
- **Disposition:** `include` — `task-attachments` is an allow-listed root; the
  key is a flat safe token (no absolute path, no traversal vector). **Gated:**
  the include is valid only once the future capture/import validation rejects
  in-root symlinks and nonregular members (today an in-root symlink is
  admitted, so current behavior ≠ required portability boundary).
- **Fixtures:** FX-C1-OK, FX-C1-MISSING, FX-C1-ESCAPE, FX-C1-SYMLINK,
  FX-C1-INROOT-SYMLINK, FX-C1-NONREGULAR.

### C2 — `thread_message_attachments.artifact_name` → shared `ArtifactStore`

- **Producer:** `thread_message_attachments.artifact_name` (TEXT); a *nested*
  artifact key (e.g. `reports/2026/q2.pdf`). `database.py` lines ~923–937.
- **Resolver:** `runtime/infrastructure/artifact_store.py`
  `ArtifactStore.path_for(name)` → `self._root / name`; `validate_name`
  rejects absolute/trailing `/`, `//`, `\`, empty/`..`/dot-leading segments,
  and asserts `is_relative_to(root.resolve())`.
- **Call sites:** `runtime/daemon/routes/threads.py` `_normalize_attachments`
  (line ~243 `store = ArtifactStore(OrgPaths(org.root).artifacts_dir)`,
  line ~257 `store.path_for(artifact_name)`).
- **Root:** `<org>/artifacts/<nested key>`.
- **Disposition:** `include` — `artifacts` is allow-listed; keys are validated
  and contained.
- **Fixtures:** FX-C2-OK, FX-C2-MISSING, FX-C2-ESCAPE, FX-C2-SYMLINK.

### C3 — `thread_scoped_attachments.attachment_id` → `ThreadScopedAttachmentStore`

- **Producer:** `thread_scoped_attachments.attachment_id` (TEXT UNIQUE) plus
  `thread_id` (TEXT, FK → `threads.id`); both are DB-held identifiers that are
  concatenated into a path. `database.py` lines ~940–951.
- **Resolver:** `runtime/infrastructure/thread_scoped_attachment_store.py`
  `ThreadScopedAttachmentStore.path_for(thread_id, attachment_id)` →
  `_attachments_dir(thread_id) / attachment_id` =
  `<threads_root>/<thread_id>/attachments/<attachment_id>` (`:26–32`);
  `_attachments_dir` also `mkdir(parents=True)`s the resulting path.
- **Call sites:** `runtime/daemon/routes/threads.py` `_attachment_store(org)`
  (line ~2810 constructs the store at `OrgPaths(org.root).threads_dir`);
  reads at line ~2909 (`store.read(thread_id, attachment_id)` → `:54–60`
  follows the path), writes at ~2960.
- **Root:** `<org>/threads/<thread_id>/attachments/<attachment_id>`.
- **Containment/identifier validation (actual current behavior):** **none.**
  `path_for` concatenates the two DB values and returns the path with no
  segment validation, no separator/`..`/absolute rejection, and no
  symlink/nonregular check; `read()` follows it. Route-created `thread_id`
  (`THR-NNN`) and `attachment_id` (`org.db.next_thread_attachment_id()`) are
  generated, **but a staged/imported DB is the trust boundary this document
  covers, and route generation does not secure it**. The org-root classifier
  only inspects direct org children, so it does not catch a malicious nested
  identifier.
- **Validation/integrity (required future portability validation — not present
  today):** before any `include`, `thread_id` and `attachment_id` must each be
  validated as a **single safe path segment** — nonempty, matching a safe-token
  regex (no `/`, `\`, NUL, no `..`, no leading `.`), not absolute — and the
  fully-resolved path must remain within `<org>/threads/<thread_id>/attachments/`;
  additionally the target must be a regular file (reject symlink and nonregular)
  at capture/import, fail closed.
- **Recoverability:** missing file → `KeyError` (`attachment … not found in
  thread …`) at read; an escaping identifier is **not** rejected today and
  would resolve outside the root.
- **Disposition:** `conditional` / **reject-until-staged-validation** — `threads`
  is allow-listed, but the DB identifiers are unvalidated today. The row is
  portable **only after** the staged-DB containment/segment + symlink/nonregular
  validation above is enforced; until then a malicious DB identifier must be
  treated as a refusal (`escape`/`nonregular`), not included. Do **not** claim
  route-generated IDs secure an imported DB.
- **Fixtures:** FX-C3-OK, FX-C3-MISSING, FX-C3-ESCAPE, FX-C3-SYMLINK,
  FX-C3-NONREGULAR.

### C4 — `jobs.stdout_path` / `jobs.stderr_path` → direct `open()` by **absolute** path

- **Producer:** `jobs.stdout_path`, `jobs.stderr_path` (TEXT). `database.py`
  lines ~995–1027. **Stored shape is an absolute path** (verified live:
  `/…/orgs/happyranch/jobs/JOB-001.out`).
- **Resolver:** *no* store class — the value is opened directly:
  - `runtime/daemon/routes/jobs.py` line ~513 `open(path, "r")` for tail;
  - `_read()` lines ~921–932 `Path(path).read_bytes()` for the full stream.
- **Write site:** `routes/jobs.py` lines ~744–761
  `stdout_path = jobs_dir / f"{job_id}.out"` then `str(stdout_path)` persisted.
- **Root:** `<org>/jobs/JOB-NNN.{out,err}` (allowed), but the DB stores the
  **absolute** spelling.
- **Validation/integrity:** none at read (missing → empty result). A
  `database.py` migration (lines ~302–310) rewrites `/scripts/SR-` → `/jobs/JOB-`
  in these columns — evidence that path strings in DB are subject to
  machine-layout drift.
- **Recoverability:** missing file → empty stream (no error).
- **Disposition:** `target-local` (files include, DB value re-resolved).
  This is the **only resolution-bearing absolute path** among all consumers.
  After same-slug relocation the `jobs/*.out|err` bytes are portable and
  carried (root allow-listed), but the absolute path strings must be rebased to
  the destination org root at import — or the column must be re-derived from
  `job_id` on the destination. **Flagged** as the highest-risk import-time
  re-resolution point; Slice B/C must own it (see §9 — it is classified, so it
  is *not* a STOP).
- **Fixtures:** FX-C4-ABS, FX-C4-MISSING.

### C5 — `jobs.cwd_hint` / `jobs.cwd_resolved` → workspace cwd

- **Producer:** `jobs.cwd_hint` (TEXT, *relative*, validated by
  `_validate_cwd_hint` at `routes/jobs.py:89` — rejects absolute and `..`);
  `jobs.cwd_resolved` (TEXT, absolute, display/inspection only).
- **Resolver:** `routes/jobs.py:669` `(workspace_root / cwd_hint).resolve()`,
  re-derived at spawn time (line ~719 `_resolve_cwd`).
- **Root:** `<org>/workspaces/<agent>/<cwd_hint>` — **workspace data**.
- **Disposition:** `exclude` — workspace non-memory data is excluded
  (`EXCLUDE_WORKSPACE_NON_MEMORY`). The relative `cwd_hint` string is carried
  harmlessly but points into excluded bytes; `cwd_resolved` is a stale-able
  absolute display field.
- **Fixtures:** FX-C5-REL (adversarial absolute/dotdot rejected at input).

### C6 — `dreams.transcript_path` → `DreamStore` (display-only; re-derived)

- **Producer:** `dreams.transcript_path` (TEXT). `database.py` lines ~733–753.
  Stored shape is **absolute** (verified live).
- **Resolver (read):** `runtime/daemon/routes/dreams.py:117`
  `_store(org).read_transcript(dream_id)` → `DreamStore.path_for(dream_id)` =
  `<org>/dreams/<dream_id>.md`. **Resolution uses `dream_id`, not the stored
  `transcript_path`.** `transcript_path` is only consulted as a truthy guard
  (`if dream.transcript_path:`) and echoed in the API payload.
- **Write site:** `routes/dreams.py:190–207` `str(transcript_path)` after
  `DreamStore.write_transcript`.
- **Disposition:** `include` — `dreams` is allow-listed; the `.md` bytes are
  portable and are re-derived from `dream_id` at the destination. The absolute
  `transcript_path` value is display-only and will be stale after relocation
  (target-local cosmetic concern, not a resolution dependency).
- **Fixtures:** FX-C6-ABS.

### C7 — `threads.transcript_path` → `ThreadStore` (derived; display-only)

- **Producer:** `threads.transcript_path` (TEXT). `database.py` lines ~877–891.
  Stored absolute (verified live).
- **Resolver (read):** none by path. The transcript is a **derived artifact**
  re-rendered from `thread_messages` via `render_transcript_body`
  (`routes/threads.py:2676`) at archive/close time; `transcript_path` is only
  set (`set_thread_transcript_path`, line ~2689) and echoed.
- **Disposition:** `include` — `threads` is allow-listed; the `.md` is
  regenerable from the DB and its absolute path value is display-only.
- **Fixtures:** FX-C7-ABS.

### C8 — `schedules.transcript_path` → display-only

- **Producer:** `schedules.transcript_path` (TEXT). `database.py` lines ~805–830.
  Stored absolute.
- **Resolver (read):** none by path. Written at
  `routes/schedules.py:443` `_write_schedule_transcript` →
  `<org>/schedules/<schedule_id>.md`, persisted `str(...)`; read is
  display-only.
- **Disposition:** `include` — `schedules` is allow-listed; path value
  display-only.
- **Fixtures:** FX-C8-ABS.

### C9 — `work_hours.transcript_path` → display-only

- **Producer:** `work_hours.transcript_path` (TEXT). `database.py` lines ~779–800.
  Stored absolute.
- **Resolver (read):** none by path. Written at
  `routes/work_hours.py:63` `_write_wake_transcript` →
  `<org>/work_hours/<id>.md`, persisted `str(...)`; read is display-only.
- **Disposition:** `include` — `work_hours` is allow-listed; path value
  display-only.
- **Fixtures:** FX-C9-ABS.

### C10 — `tasks.final_output_dir` / `task_results.output_dir` → workspace output (excluded)

- **Producer:** `tasks.final_output_dir`, `task_results.output_dir` (TEXT,
  **relative**, agent-supplied). `database.py` lines ~676 and ~729.
- **Resolver:** `routes/tasks.py:416` `_read_output(workspaces_dir,
  assigned_agent, output_dir)` → `(workspaces/<agent>/<output_dir>).resolve()`
  with `is_relative_to(agent_root)` containment. Absolute/`..` escape → `None`
  (unresolvable).
- **Root:** `<org>/workspaces/<agent>/<output_dir>` — workspace task output.
- **Disposition:** `exclude` — the pointed-at bytes are `EXCLUDE_TASK_OUTPUT`.
  The relative path string is carried but dangles after import (target-local).
  A historical `database.py` migration (lines ~1219–1229) rewrites `artifacts/%`
  → `output/%`, showing these relative paths have already been re-based once.
- **Fixtures:** FX-C10-REL.

### C11 — `custom_skill_versions.content_artifact_key` → `ArtifactStore`

- **Producer:** `custom_skill_versions.content_artifact_key` (TEXT) =
  `custom-skills/<slug>/<digest>/SKILL.md`; paired with `content_hash` =
  `sha256(skill_md)`. `database.py` lines ~1083–1102.
- **Resolver:** `runtime/infrastructure/artifact_store.py` `read()`:
  - materialization read: `runtime/orchestrator/workspace_adapters.py:801,829`
    `artifact_store.read(artifact_key)` (line ~801 constructs
    `ArtifactStore(OrgPaths(org_root).artifacts_dir)`);
  - recovery read: `runtime/daemon/routes/skills.py:1188`.
- **Write site:** `runtime/daemon/routes/custom_skills.py:27`
  `ArtifactStore(artifacts_dir).put(f"custom-skills/{slug}/{digest}/SKILL.md", …)`.
- **Root:** `<org>/artifacts/custom-skills/<slug>/<digest>/SKILL.md`.
- **Validation/integrity:** `content_hash` must equal the SHA-256 of the
  artifact bytes (asserted at recovery, `skills.py:1193`); the machine-global
  canonical package is rebuilt from this artifact + hash.
- **Disposition:** `include` — `artifacts` is allow-listed; the hash↔bytes
  binding is the import-time integrity check.
- **Fixtures:** FX-C11-OK, FX-C11-HASHMISMATCH, FX-C11-MISSING.

### C12 — `custom_skill_versions.skill_md_cache` / `references_manifest` / `assets_manifest` → inline / no filesystem reference

- **Producer:** `custom_skill_versions.skill_md_cache` (inline SKILL.md text),
  `references_manifest` (TEXT JSON), `assets_manifest` (TEXT JSON).
  `database.py` lines ~1083–1102.
- **Resolver:** none to filesystem bytes. `skill_md_cache` is a denormalized
  inline copy used for diff/validation display. `references_manifest` /
  `assets_manifest` are persisted/relayed by `runtime/skills/custom_store.py`
  but have **no resolver that reads them to filesystem bytes** on current main
  (the canonical-store/materializer path builds from the `content_artifact_key`
  artifact, not from these manifests).
- **Disposition:** `include` (rows are portable DB data with no path
  dependency). Recorded here so a future resolver that *does* dereference these
  manifests re-enters the map.
- **Fixtures:** FX-C12-INLINE.

### C13 — `skill_lifecycle_packages.content_artifact_key` → `ArtifactStore.delete` (legacy, constructor-time)

- **Producer table/field/shape:** legacy `skill_lifecycle_packages` table
  (retired; **not** created by current `_create_tables`), column
  `content_artifact_key` (TEXT, nullable) — a **nested artifact key** (relative
  path in the org artifact store, e.g. `custom-skills/<slug>/<digest>/SKILL.md`)
  written by `ArtifactStore.put(...)` under the pre-canonical skill-lifecycle
  pilot (THR-055). Distinct from `custom_skill_versions.content_artifact_key`
  (C11).
- **Resolver + direct production call site:** `Database.__init__`
  (`database.py:153`) calls `self._retire_skill_lifecycle_if_present()` at
  `:170` — **before** `self._create_tables()` at `:171`, and after
  `_migrate_jobs_table_if_needed` / `_migrate_drop_talk_surface_if_needed`
  (`:167–168`). The retire routine (`:199–240`) selects every non-null
  `content_artifact_key` (`:212–218`), drops the `skill_lifecycle_%` tables in
  one transaction (`:220–230`), then constructs
  `store = ArtifactStore(self.db_path.parent / "artifacts")` (`:235`) and calls
  `store.delete(artifact_key)` per key (`:236–239`), swallowing only
  `ArtifactNotFound`.
- **Action/path semantics:** this is a **destructive delete** (unlink of the
  artifact blob under `<org>/artifacts/<key>`), not a read, and it fires on
  **every** `Database(...)` construction whenever a legacy
  `skill_lifecycle_%` table is still present. It therefore mutates the source
  DB (drops tables) and the source artifact tree (deletes blobs) as a
  constructor side effect.
- **Validation/integrity (actual current behavior):** `ArtifactStore.delete`
  runs `validate_name` + `path_for` (so an escaping/absolute key raises
  `InvalidArtifactName`, which is **not** caught here and would crash the
  constructor), then `unlink()`s whatever regular-or-symlink path passes
  containment (an in-root symlink is unlinked; an out-of-root symlink raises
  `path_traversal`). A missing artifact raises `ArtifactNotFound`, which is
  swallowed (no-op). No symlink/nonregular distinction beyond
  `validate_name`/containment is made.
- **Recoverability:** missing artifact → `ArtifactNotFound` (swallowed, no-op);
  invalid/escape key → `InvalidArtifactName` escapes the constructor (crash
  vector); symlink → unlinked (the link removed, its target untouched).
- **Disposition/detection requirement:** `reject`-until-retired. A legacy
  `skill_lifecycle_%` table must be detected by a **read-only pre-connection
  schema inspection** (e.g. `SELECT name FROM sqlite_master WHERE name LIKE
  'skill_lifecycle_%'` on a read-only connection, or equivalent raw-file
  inspection) **before any `Database(...)` construction** — never by
  constructing a `Database`, because that would itself fire the destructive
  retire (see §5's detect-before-any-constructor rule). If a legacy table with
  any non-null `content_artifact_key` is present, the exporter refuses
  (`legacy_skill_lifecycle_unretired`); the source must be retired by the normal
  daemon (or deliberately) before export. On any healthy DB the table is
  already dropped and the referenced blobs already deleted.
- **Fixtures:** FX-C13-ABSENT, FX-C13-ESCAPE, FX-C13-MISSING, FX-C13-SYMLINK.

---

## 3. "No consumer found" — tables/fields inspected without a filesystem resolver

The following were inspected and **do not** resolve DB-held data to filesystem
bytes (or the reference is a slug/id, not a path). They are documented to
prevent a future implementer from assuming a path dependency exists.

| Table / field | Why no filesystem resolver |
| --- | --- |
| `kb_views.slug`, `dream_kb_candidates.promoted_kb_slug` | KB is filesystem-only; the DB holds **slugs**, resolved by `kb_store.path_for(slug)` at load, not stored paths. KB files under `kb/` are allow-listed but are not *referenced by* a DB path column. |
| `skill_validation_events.skill_id` / `slug` | Legacy-skill validation rows key on id/slug; no path column. The legacy `skills/<pkg>/` tree is classified by `_classify_skills` from the filesystem, not from a DB path. |
| `audit_log` (all columns) | `task_id` is a scope id (with `artifact:`/`config:`/`TASK-` prefixes), not a path. |
| `org_settings.value_json` | JSON settings (dreaming, threads, session_timeout, working_hours). No file-path values. |
| `escalation_notifications` / `processed_event_ids` | Dormant Feishu tables (THR-022 removed); no filesystem reference. |
| `session_token_usage`, `tasks.brief`, `thread_messages.body_markdown` | Inline text. |
| Learnings/memory `workspaces/<agent>/memory/**` | Filesystem-only (no DB table). Allow-listed as the sole workspace carve-out; not DB-referenced. |
| `org/` content (`charter.md`, `teams.yaml`, `config.yaml`, `agents/*.md`) | Loaded from filesystem (`TeamsRegistry`, `org_config`); no DB path column. |

**Machine-global surfaces (excluded from the archive; not per-org):**

| Surface | Location | Why excluded |
| --- | --- | --- |
| Canonical skill store | `~/.happyranch/canonical-skills/` (or `settings.daemon_home/canonical-skills`), `runtime/skills/canonical_store.py:_get_canonical_store_root` | Hash-addressed machine-global package store; rebuilt from the per-org artifact (`content_artifact_key`) + `content_hash`. |
| Metrics DB | `<runtime_root>/metrics.db` (`metrics_store.py`) | Daemon-global, append-only; not org data. |
| Direct-connect authority DB | `<runtime_root>/direct_connect_authority.db` (`state.py:67–68`) | Machine-global. |
| Daemon home | `daemon.pid`, `daemon.port`, `daemon.token`, `runtimes.yaml`, `config.yaml` | Machine-specific lifecycle/credential files. |

**Accounting note — `skill_lifecycle_packages` is a consumer, not "no
consumer".** The legacy `skill_lifecycle_packages.content_artifact_key` column
*does* resolve DB-held data to filesystem bytes (via `ArtifactStore.delete` at
`Database.__init__`), so it is mapped as **C13 in §2**, not listed here. A
future implementer must not assume the legacy lifecycle tables are inert — they
carry a destructive constructor-side-effect consumer (see C13). The distinct
current-v2 `custom_skill_versions.content_artifact_key` is C11.

---

## 4. Fixture registry

Keyed by fixture ID. Each row cites a consumer-map row (§2) and provides the
minimal staged source DB/file shape, the intended consumer, and the adversarial
variants with expected failure category and whether source/destination is
touched. Fixtures are *design specifications* for the Slice B/C test harness
(they are **not** implemented in this gate).

Failure categories: `refusal` (named rejection, no archive), `missing` (empty/
fallback), `integrity` (hash/content mismatch), `escape` (traversal/absolute),
`nonregular` (symlink/special).

| Fixture ID | Consumer | Source DB shape (producer row) | Source file shape | Adversarial variant | Expected failure | Source touched? | Dest touched? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FX-C1-OK | C1 | `task_attachments(task_id=T-1, storage_key="abc123", …)` | `task-attachments/abc123` (regular) | — | none (included) | no | no |
| FX-C1-MISSING | C1 | same row | file absent | missing-file | `missing` (refusal/empty on read) | no | no |
| FX-C1-ESCAPE | C1 | row with `storage_key="../evil"` | — | escaped-path | `escape` (validate_storage_key rejects `..`) | no | no |
| FX-C1-SYMLINK | C1 | row with `storage_key="link"` | `task-attachments/link → /etc/passwd` (outside root) | symlink (resolved target escapes root) | `escape` (path_for containment rejects resolved target) | no | no |
| FX-C1-INROOT-SYMLINK | C1 | row with `storage_key="link"` | `task-attachments/link → task-attachments/other` (in-root) | in-root symlink | `nonregular` — current code **admits** (containment passes, `read()` follows); required capture/import must refuse fail-closed | no | no |
| FX-C1-NONREGULAR | C1 | row with `storage_key="fifo1"` | `task-attachments/fifo1` (FIFO/special) | nonregular member | `nonregular` — required capture/import must refuse | no | no |
| FX-C2-OK | C2 | `thread_message_attachments(artifact_name="reports/q2.pdf")` | `artifacts/reports/q2.pdf` (regular) | — | none (included) | no | no |
| FX-C2-MISSING | C2 | same row | file absent | missing-file | `missing` | no | no |
| FX-C2-ESCAPE | C2 | `artifact_name="../../etc/passwd"` | — | escaped-path | `escape` (validate_name rejects) | no | no |
| FX-C2-SYMLINK | C2 | `artifact_name="reports/x"` | `artifacts/reports/x → outside` | symlink/nonregular | `nonregular` | no | no |
| FX-C3-OK | C3 | `thread_scoped_attachments(attachment_id="att-1", thread_id="THR-1")` | `threads/THR-1/attachments/att-1` | — | none (included) | no | no |
| FX-C3-MISSING | C3 | same row | file absent | missing-file | `missing` | no | no |
| FX-C3-ESCAPE | C3 | `thread_scoped_attachments(thread_id="../..", attachment_id="att-1")` | — | staged-DB escape identifier | `escape` — no validation today (would resolve outside root); required segment validation refuses | no | no |
| FX-C3-SYMLINK | C3 | same row, `attachment_id="att-1"` | `threads/THR-1/attachments/att-1 → outside` | symlink/nonregular | `nonregular` — required capture/import refuses | no | no |
| FX-C3-NONREGULAR | C3 | same row | `threads/THR-1/attachments/att-1` (FIFO/special) | nonregular member | `nonregular` — required capture/import refuses | no | no |
| FX-C4-ABS | C4 | `jobs(id="JOB-9", stdout_path="<src>/jobs/JOB-9.out", stderr_path="<src>/jobs/JOB-9.err")` | `jobs/JOB-9.out`, `jobs/JOB-9.err` | **absolute path re-resolution** | import-time rebase (target-local) — see §9 | read-only | yes (rebase) |
| FX-C4-MISSING | C4 | same row | files absent | missing-file | `missing` (empty stream) | no | no |
| FX-C5-REL | C5 | `jobs(cwd_hint="../escape")` | — | escaped-path (input) | `escape` (`_validate_cwd_hint` rejects `..`) | no | no |
| FX-C6-ABS | C6 | `dreams(id="DREAM-1", transcript_path="<src>/dreams/DREAM-1.md")` | `dreams/DREAM-1.md` | stale absolute (display-only) | none — re-derived from `dream_id` | read-only | no |
| FX-C7-ABS | C7 | `threads(id="THR-1", transcript_path="<src>/threads/THR-1.md")` | `threads/THR-1.md` | stale absolute (derived) | none — regenerable | read-only | no |
| FX-C8-ABS | C8 | `schedules(id="SCHEDULE-1", transcript_path="<src>/schedules/SCHEDULE-1.md")` | `schedules/SCHEDULE-1.md` | stale absolute (display-only) | none | read-only | no |
| FX-C9-ABS | C9 | `work_hours(id="WORKHOUR-1", transcript_path="<src>/work_hours/WORKHOUR-1.md")` | `work_hours/WORKHOUR-1.md` | stale absolute (display-only) | none | read-only | no |
| FX-C10-REL | C10 | `tasks(id="T-1", final_output_dir="output/T-1")` | `workspaces/a/output/T-1/*` | excluded workspace output | `missing`-after-import (dangling relative ref) | no | no |
| FX-C11-OK | C11 | `custom_skill_versions(content_artifact_key="custom-skills/s/digest/SKILL.md", content_hash=<sha256>)` | `artifacts/custom-skills/s/digest/SKILL.md` matching hash | — | none (included; hash verified) | read-only | no |
| FX-C11-HASHMISMATCH | C11 | same row, `content_hash=<wrong>` | file bytes differ | hash/content mismatch | `integrity` (refusal) | no | no |
| FX-C11-MISSING | C11 | same row | file absent | missing-file | `missing` (refusal) | no | no |
| FX-C12-INLINE | C12 | `custom_skill_versions(skill_md_cache="…", references_manifest=null, assets_manifest=null)` | none | inline only | none (no fs ref) | no | no |
| FX-C13-ABSENT | C13 | no `skill_lifecycle_%` table (current-v2 DB) | none | — | none (no consumer; proceed) | no | no |
| FX-C13-ESCAPE | C13 | `skill_lifecycle_packages(content_artifact_key="../../etc/passwd")` | — | invalid/escape key | `escape` — read-only detector refuses **before** any `Database(...)` (were the retire to run, `InvalidArtifactName` would crash the constructor) | no | no |
| FX-C13-MISSING | C13 | `skill_lifecycle_packages(content_artifact_key="custom-skills/s/digest/SKILL.md")` | artifact file absent | missing-file | `missing` — `ArtifactNotFound` swallowed (no-op) if the retire ran; detector still refuses before any construction | no | no |
| FX-C13-SYMLINK | C13 | same row | `artifacts/custom-skills/s/digest/SKILL.md` → in-root symlink | symlink/nonregular | `nonregular` — detector refuses (or, were the retire to run, `unlink()` removes the link only) | no | no |

Every consumer-map row in §2 has at least one fixture; every fixture names a
specific consumer.

---

## 5. Source & destination compatibility detection matrix

Detection order is **fixed and precedes every effect**: classify/detect
*before* any `Database(...)` constructor/connection, migration, org-state load,
archive extraction, staging mutation, or conversion. Every non-current case is
a **named refusal** — never auto-upgrade, conversion, or best effort.

### Verified fact vs proposed seam

- **Verified (current main):** the only compatibility gate that exists today is
  `RuntimeDir.load` (`runtime/runtime.py:114–123`) requiring the runtime marker
  `happyranch.yaml` with `schema_version == 2`; any other value raises
  `ValueError`. `RuntimeDir.iter_org_roots` (`runtime.py:73–96`) recognizes an
  org only by `org/teams.yaml` and skips reserved `_pending`/`_archive`.
- **Proposed (future seam, not implemented):** an explicit v0/v1 *flat
  single-org* detector and an explicit *malformed-layout* detector are
  deliverables of the offline foundation slice, **not** present on current main.
  The matrix below therefore marks those rows as "detector to be built" and must
  not be read as an existing code path. (Consultant review seq-188: "make the
  detector an explicit deliverable rather than inheriting the docs sentence.")

### Source-side matrix

| Source layout | Detection | Disposition (named refusal) |
| --- | --- | --- |
| current-v2 multi-org root, `schema_version: 2`, `org/teams.yaml` present, slug valid | `RuntimeDir.load` + `iter_org_roots` | eligible candidate (proceed to preflight/classifier) |
| v0 flat single-org (no `happyranch.yaml`, legacy shape) | **detector to be built** | `refused_v0_layout` — no conversion |
| v1 flat single-org (legacy shape) | **detector to be built** | `refused_v1_layout` — no conversion |
| `schema_version` missing / malformed / ≠2 | `RuntimeDir.load` (verified) | `refused_schema_version` |
| `happyranch.yaml` absent (not a runtime) | `RuntimeDir.load` (verified) | `refused_not_runtime` |
| `org/teams.yaml` absent | `iter_org_roots` (verified) | not-yet-initialized — skipped, not migrated |
| mixed/ambiguous root (some v2, some v0/v1 children) | per-child classification | per-child named refusal; no partial conversion |
| malformed org member (unknown root, nonregular, nonzero residue, invalid skill) | `classify_root_entries` (verified) | `rejected_<reason>` |

### Destination-side matrix

| Destination layout | Detection | Disposition |
| --- | --- | --- |
| current-v2 runtime, target slug absent | `iter_org_roots` | eligible publish target |
| target slug present / nonempty | collision check | `refused_collision` / `refused_nonempty_target` |
| `orgs/_pending/` or `_archive/` present | reserved-slug check (verified) | staging residue — not a publish target |
| non-v2 runtime marker | `RuntimeDir.load` | `refused_schema_version` |
| destination marker/tree nonregular (symlink etc.) | `_is_regular_entry` | `refused_nonregular` |

No matrix row alters schema or implies v0/v1 conversion. If proof during a
later slice reveals an existing overloaded-column or compatibility semantic
that would need change, that is a STOP/escalation (not solved by this document).

---

## 6. Source-sidecar capture audit/harness design (not implementation)

This section **designs** the audited capture seam and the fixtures that prove
every control fires. It names the boundary and the spy seam; it does not write
production or test code.

### Named audited helper boundary and connection-spy seam

- **Audited helper:** a single named offline-maintenance capture function that
  is the *only* code path allowed to open the source SQLite DB. It accepts the
  resolved source DB path, records its own command-owned temporary sidecars by
  absolute path + inode before creating them, and removes exactly those on the
  way out.
- **Connection-spy seam:** a `sqlite3.connect`-level spy (monkeypatched at the
  `Database.__init__` / `sqlite3.connect` boundary in the harness) that counts
  every source-DB connection opened during an export attempt. Fixtures (a) and
  (b) assert this count is **zero** for refusal paths.
- **D0/D1 inventory:** before any connection, capture `D0` = every direct
  entry of the source DB-parent directory (`name, type, size, inode/device,
  sha256 for regular files`) plus the classifier-driven included-tree
  inventory `T0`. After close/cleanup, recapture `D1`/`T1`; require exact
  equality. Command-owned staging/output lives *outside* the source DB-parent
  so it cannot contaminate the comparison.
- **Ownership/cleanup:** every command-created temporary sidecar (snapshot
  `-wal`/`-shm`, source-side temporaries) is recorded and removed before
  success; nothing that existed in `D0` is ever deleted. A second export
  against the same stopped fixture must succeed (no self-created residue).

### Fixture (a) — pre-connection existing `-wal` and separately `-shm` hard-refusal

Create a stopped fixture whose source DB directory contains `happyranch.db-wal`
(and, in a separate run, `happyranch.db-shm`) with plausible nonzero content.
Assert: the export refuses with `source_sidecar_present`, produces no archive,
names the exact sidecar path, and **the connection spy reports zero source
SQLite connections**. The two sidecars are tested *separately* so each is
independently refusal-worthy. **Source is untouched.**

### Fixture (b) — live-but-idle daemon fails closed through preexisting sidecars

Build a fixture where a source DB has live sidecars present *and* the nominated
daemon-home evidence (pid/port/registry) is deliberately absent/clean — the
state where lock-contention (`BEGIN IMMEDIATE`) and daemon-home checks would
both read "stopped". Assert the sidecar presence alone still refuses, and the
spy reports zero source connections. This proves the gate does not depend on
lock acquisition or daemon-home accuracy. **Source is untouched.**

### Fixture (c) — successful exporter preserves source exactly; second run succeeds

On a cleanly-stopped fixture (no pre-existing sidecars), run the audited
capture to completion. Assert: post-close `D1 == D0` and `T1 == T0` (no
`-wal`/`-shm`, no new sentinel, no residue at the source org root); the
archive contains the logical backup but no source sidecars. Then run a second
export on the identical fixture and assert it also succeeds (the sidecar gate
does not trip on the first run's own residue because the audited helper removed
everything it created). **Source is untouched; destination staging is private.**

### Fixture (d) — committed-but-not-checkpointed WAL: raw copy loses rows, backup preserves

Construct a crash-shaped source DB: 500 rows **committed but not
checkpointed**, leaving `happyranch.db-wal` nonzero. Assert:

- a raw copy of `happyranch.db` alone reads **0** rows (silent, checksum-valid
  data loss — the founder's seq-168 "just tar the folder" question answered
  empirically);
- `sqlite3.Connection.backup()` reads **500** rows.

**Framing (binding, per QA seq-204 / consultant seq-203):** this is
**defense-in-depth behind the sidecar gate**, *not* an independently reachable
successful export state. The sidecar gate refuses the crash-shaped state before
any connection; `Connection.backup()` only matters if that gate is ever
weakened. Two controls target the same failure from opposite sides.

**Explicitly rejected/omitted:** the *false uncommitted-transaction* fixture
(open a write transaction, do not commit, expect raw copy ≠ backup). It cannot
fail: an uncommitted insert never reaches the WAL (page cache holds it), so
both raw copy and backup see 0 rows; and it exercises a live uncommitted writer
holding the write lock — a state the sidecar gate is supposed to refuse, so the
exporter could never reach it. QA withdrew this fixture (seq-204).

### BEGIN IMMEDIATE framing (binding)

`BEGIN IMMEDIATE` (or an equivalently tested exclusion primitive) is held only
as a **future-writer exclusion during capture**, never as a stopped-daemon or
liveness proof. It blocks a writer that begins *after* the guard; it says
nothing about an already live-but-idle daemon (verified: `BEGIN IMMEDIATE`
succeeds against an idle open connection). There is **no source checkpoint**,
and **pre-existing source sidecars are never deleted** — they are refusal
evidence, not cleanup targets.

---

## 7. Classifier policy and target-only marker contract (preserved)

Preserved from TASK-5426; stated here only to the extent they bear on consumer
disposition.

- **Classifier-not-`ALLOWED_ROOTS`.** The offline capture walks
  `classify_root_entries()` output only, **never** `ALLOWED_ROOTS` directly.
  `skills` and `workspaces` are *not* in `ALLOWED_ROOTS`
  (`roots.py:58–72`); they are special-cased in `_classify_skills` /
  `_classify_workspaces`. Iterating `ALLOWED_ROOTS` would silently drop agent
  memory (`workspaces/*/memory/**`) and valid legacy skills.
- **Target-only inactive marker.** Exactly one durable target-only handoff
  record: regular file `org/.happyranch-imported-inactive.json`, generated in
  private staging before publish. Export excludes it; import rejects an archive
  that supplies it. Valid marker → `imported_inactive`; corrupt/nonregular →
  `invalid_import_state`; absent → ordinary org. No second provenance record.
  (Not implemented in this gate.)

---

## 8. Deliverable and diff radius

This gate delivers one new file:

```
docs/superpowers/specs/org-portability-reference-consumers.md
```

No production code, test code, schema, auth, permission, or runtime data is
changed. The diff is documentation-only.

---

## 9. Escalation triggers (STOP — do not invent disposition)

The following, if encountered during a later slice, must be escalated rather
than resolved by documentation invention:

- any **unclassified** DB→file consumer not in §2/§3;
- a resolution-bearing **absolute source path outside the org root** or a path
  escaping the allowable root (note: §2 C4's *within-root* absolute paths are
  classified as `target-local` and are not this trigger);
- a required file outside the approved classifier policy;
- any schema/migration or overloaded-column meaning change;
- any auth/credential or permission-model change;
- any v0/v1 conversion or unsupported compatibility claim.

**Flagged for Slice B/C ownership (classified, not escalated):** the
`jobs.stdout_path` / `jobs.stderr_path` absolute-path re-resolution (C4) is the
single highest-risk import-time re-resolution point; it must be addressed with
an explicit rebase-or-rederive rule before import lands, and covered by a
fixture (FX-C4-ABS) that asserts the destination reads the rebased path.
