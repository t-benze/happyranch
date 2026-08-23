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
(read-only) to confirm stored shapes, plus the recorded PRAGMA
schema/population audit in §2A.

---

## 2. DB-to-filesystem consumer map

Consumers are traced from the **producer table → stored column → resolver →
path-construction → validation → recoverability → disposition**. A "consumer"
here means a column whose value is resolved (at read time, or at the sole
write site) to filesystem bytes under the org root.

> The authoritative classification is the generated coverage ledger in §2A; this
> section is a compact cross-reference to it. Where they disagree, §2A governs
> (and the discrepancy is a default refusal, never a silent omission).

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
  rejects absolute/trailing `/`, `//`, `\`, empty/`..`/dot-leading segments.
  `path_for` then resolves the target and asserts the **resolved** target stays
  inside the store root (`artifact_store.py:55–75`).
- **Call sites:** `runtime/daemon/routes/threads.py` `_normalize_attachments`
  (line ~243 `store = ArtifactStore(OrgPaths(org.root).artifacts_dir)`,
  line ~257 `store.path_for(artifact_name)`; line ~270 `path.exists()` and
  line ~278 `path.stat()` then follow the resolved path to build the
  attachment record).
- **Root:** `<org>/artifacts/<nested key>`.
- **Traversal/symlink containment (actual current behavior):**
  `validate_name` rejects traversal at the string level; `path_for` resolves
  and asserts in-root containment. This rejects a symlink whose resolved target
  escapes the root (`path_traversal` → `escape`), but it does **not** reject
  symlinks as such: an **in-root symlink** (a link whose target is another file
  inside `artifacts/`) passes containment, and `path.exists()` / `path.stat()`
  subsequently follow it (`threads.py:270,278`). The org-root classifier
  (`roots.py:122–155`) inspects only direct org children, so a nested symlink
  or nonregular member under `artifacts/` is not caught there either.
- **Validation/integrity (required future portability validation — not present
  today):** capture/import must reject **every** symlink and every nonregular
  (FIFO/socket/device) member at any depth under `artifacts/`, fail closed
  (`nonregular`), **before any capture/import effect**; the string-shape +
  in-root containment check is insufficient for that boundary. This is the same
  recursive regular-file/no-symlink rule C1 requires; it is not enforced by the
  current resolver.
- **Recoverability:** missing file → `artifact_not_found` (404); invalid name →
  `invalid_artifact_name` (400; `path_traversal` for an outside-root resolved
  link, `invalid_name` for a string-shape violation).
- **Disposition:** `conditional` / **reject-until-staged-validation** —
  `artifacts` is allow-listed, but current containment does **not** protect
  nested members, so an in-root symlink is admitted and followed today. The
  include is valid **only after** the future recursive regular-file/no-symlink
  validation rejects every in-root symlink and nonregular member before any
  capture/import effect; until then a staged symlink/nonregular member must be
  treated as a refusal (`nonregular`), not included.
- **Fixtures:** FX-C2-OK, FX-C2-MISSING, FX-C2-ESCAPE, FX-C2-SYMLINK,
  FX-C2-INROOT-SYMLINK, FX-C2-NONREGULAR.

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

- **Producer:** `jobs.cwd_hint` (TEXT; **relative** only when the route-input
  validation actually fired) and `jobs.cwd_resolved` (TEXT, absolute,
  display/inspection only). Same `jobs` table as C4 (`database.py` lines
  ~995–1027).
- **Resolver:** `runtime/daemon/routes/jobs.py` `_resolve_cwd` (`:661–670`) →
  `(workspace_root / cwd_hint).resolve()` when `cwd_hint` is set; re-derived at
  spawn time (`_run_job_core` line ~719) from `record.cwd_hint` read out of the
  DB, **not** from the submit-route body.
- **Containment/validation (actual current behavior):**
  `_validate_cwd_hint` (`routes/jobs.py:89–104`) rejects absolute
  (`startswith("/")`) and `..` segments — but it runs **only** on the submit
  path (`:263`), where the value is first accepted. `_resolve_cwd` itself
  performs **no** containment check: `(workspace_root / cwd_hint).resolve()`
  with an absolute `cwd_hint` replaces `workspace_root` (path division with an
  absolute right-hand operand), and a `..`-bearing `cwd_hint` resolves above
  `workspace_root`; `.resolve()` only normalizes, it never asserts
  `is_relative_to(workspace_root)`. A **staged/imported DB** is therefore not
  protected by route-input validation: a malicious absolute or dotdot
  `cwd_hint` injected into the staged DB reaches `_resolve_cwd` unvalidated
  before a job is launched.
- **Root:** `<org>/workspaces/<agent>/<cwd_hint>` — **workspace data**
  (excluded), but a malicious absolute/`..` value resolves *outside* it.
- **Validation/integrity (required future portability validation — not present
  today):** before any imported `jobs` row can run, `cwd_hint` must be a
  **route-compatible relative workspace path** — nonempty, not absolute, no `..`
  segment, no leading `.` segment, no `\` or NUL — which **may be nested**
  (e.g. `repos/web-app`; the submit path already accepts nested relatives via
  `_validate_cwd_hint` at `jobs.py:89–104`, and persisted tests use
  `repos/web-app` — see `tests/test_database_scripts.py:66` and
  `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md:177,216`).
  **Do not reduce it to a single segment**: that would falsely refuse a valid
  imported job. Additionally the fully-resolved cwd must remain within
  `<org>/workspaces/<agent>/` (the assigned agent workspace); an absolute or
  dotdot `cwd_hint` in a staged DB is a refusal (`escape`) **before any
  capture/import effect**. This holds even though the workspace bytes themselves
  are excluded — the *string* is resolution-bearing and is executed by
  `_resolve_cwd` at spawn time, which today performs **no** containment check
  (`.resolve()` only normalizes; see §2A).
- **Recoverability:** an absolute/`..` `cwd_hint` is **not** rejected today at
  run time (only at submit) and would resolve outside `workspace_root` before a
  job is launched.
- **Disposition:** `conditional` / **reject-until-staged-validation** — the
  workspace bytes are excluded (`EXCLUDE_WORKSPACE_NON_MEMORY`), but the
  `cwd_hint` string is **not harmless**: it is a resolution-bearing value
  executed by `_resolve_cwd` before a job runs. `cwd_resolved` remains a
  stale-able absolute display/inspection field and **cannot redeem an unsafe
  `cwd_hint`**. Do **not** describe an invalid `cwd_hint` as "carried
  harmlessly."
- **Fixtures:** FX-C5-REL, FX-C5-NESTED, FX-C5-ABS, FX-C5-DOTDOT.

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
- **Traversal/symlink containment (actual current behavior):**
  `ArtifactStore.read()` (`artifact_store.py:112–118`) resolves `path_for`
  (containment) then `Path.read_bytes()`, which **follows an in-root symlink**.
  The bytes reached through such a link can hash-match `content_hash`, so the
  hash binding does **not** establish regular-file portability. The org-root
  classifier (`roots.py:122–155`) inspects only direct org children and does
  not recurse under `artifacts/`, so a nested symlink/nonregular member is not
  caught there either. An outside-root resolved link is rejected by `path_for`
  (`path_traversal` → `escape`).
- **Validation/integrity:** `content_hash` must equal the SHA-256 of the
  artifact bytes (asserted at recovery `skills.py:1193` and at materialization
  `workspace_adapters.py:831`) — retained as the import-time integrity check,
  but it is **not** sufficient on its own: because the read follows an in-root
  symlink, hash equality does not prove the referenced artifact subtree is
  regular-file and symlink-free. Required: the same recursive
  regular-file/no-symlink validation C1/C2 require, applied to the referenced
  `custom-skills/<slug>/<digest>/` subtree, **before any archive read/import
  effect**.
- **Disposition:** `conditional` / **reject-until-staged-validation** —
  `artifacts` is allow-listed, but `content_hash` alone does **not** make the
  include safe: it verifies byte content, not that the referenced artifact is a
  regular file reached without symlinks. The include is valid only once the
  recursive no-symlink/regular-file validation above is enforced before any
  effect; until then an in-root symlink/nonregular member must be treated as a
  refusal (`nonregular`).
- **Fixtures:** FX-C11-OK, FX-C11-HASHMISMATCH, FX-C11-MISSING,
  FX-C11-OUTROOT-SYMLINK, FX-C11-INROOT-SYMLINK, FX-C11-NONREGULAR.

### C12 — `custom_skill_versions.skill_md_cache` / `references_manifest` / `assets_manifest` → inline text + active (currently-unpopulated) manifest metadata

- **Producer:** `custom_skill_versions.skill_md_cache` (inline SKILL.md text,
  `TEXT`), `references_manifest` (`TEXT`, JSON), `assets_manifest` (`TEXT`,
  JSON) — all three are **active current persisted metadata** columns declared
  in `Database._create_tables()` (`database.py:1089–1091`), **not** dormant
  legacy fields. `references_manifest` / `assets_manifest` are accepted as
  optional parameters by `custom_store.create_skill_with_first_version`
  (`runtime/skills/custom_store.py:74–75, 118–119`); the production
  `service.create_version` writer (`runtime/skills/custom/service.py:38–70`)
  does **not** populate them today, and no current resolver reads them to
  filesystem bytes (the canonical-store/materializer path builds from the
  `content_artifact_key` artifact, C11, not from these manifests).
- **Population (reproducible read-only observation, recorded live):** type
  `TEXT` (nullable) per `PRAGMA table_info(custom_skill_versions)`; the live
  org DB holds **8 rows, 0 non-empty `references_manifest`, 0 non-empty
  `assets_manifest`** (contrast `skill_md_cache` = 8 non-empty,
  `content_artifact_key` = 8 non-null). This is a **currently-zero-population
  observation**, not a dormant-emptiness invariant: a non-null value must
  **not** falsely trip the §2A dormant-empty control merely because the column
  exists.
- **Disposition:** `include` as portable DB data (rows are inline text / JSON
  metadata with no direct path dependency). **If** a future producer writes
  manifest JSON that *embeds* a DB→filesystem reference, that reference is
  subject to the export-time default refusal (§2A / §6): any actual unresolved
  or out-of-policy reference observed during validation **refuses** before
  capture/import effects — it is **never** silently included merely because it
  is embedded in an active manifest value.
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
  (`artifact_store.py:120–128`) runs `validate_name` + `path_for` (so an
  escaping/absolute key raises `InvalidArtifactName`, which is **not** caught
  here and would crash the constructor), then checks `path.exists()` and
  `path.is_dir()` before `unlink()`:
  - a **non-dangling in-root symlink** (target exists) passes `path.exists()`
    and `path.is_dir()`, and is **unlinked** — the link is removed, its target
    is left untouched;
  - a **dangling in-root symlink** (target absent) makes `path.exists()` return
    `False`, so `delete` raises `ArtifactNotFound`, which the retire loop
    swallows (`database.py:236–239`) — the dangling link is **left on disk**, a
    no-op, not a crash;
  - an out-of-root symlink raises `path_traversal`; a directory target raises
    `ArtifactNotFound` via `path.is_dir()`.
  No symlink/nonregular distinction beyond `validate_name`/containment and the
  `exists()`/`is_dir()` checks is made.
- **Recoverability:** missing artifact **or dangling in-root link** →
  `ArtifactNotFound` (swallowed, no-op; a dangling link is **left on disk**);
  invalid/escape key → `InvalidArtifactName` escapes the constructor (crash
  vector); non-dangling in-root symlink → unlinked (the link removed, its target
  untouched).
- **Disposition/detection requirement:** `reject`-until-retired. A legacy
  `skill_lifecycle_%` table must be detected **before any `Database(...)`
  construction** — never by constructing a `Database`, because that would
  itself fire the destructive retire. Detection follows the fixed source
  ordering (§5/§6): **first** filesystem/layout validation and the DB-parent
  `-wal`/`-shm` scan (an existing sidecar → `source_sidecar_present` refusal,
  connection spy at **zero** source SQLite connections); **only after** that
  clean scan may a **raw read-only SQLite connection** inspect `sqlite_master`
  for legacy `skill_lifecycle_%` tables (`SELECT name FROM sqlite_master WHERE
  name LIKE 'skill_lifecycle_%'`); that read-only inspection is **still
  strictly before every `Database(...)` construction**. If a legacy table with
  any non-null `content_artifact_key` is present, the exporter refuses
  (`legacy_skill_lifecycle_unretired`); the source must be retired by the normal
  daemon (or deliberately) before export. On any healthy DB the table is
  already dropped and the referenced blobs already deleted.
- **Fixtures:** FX-C13-ABSENT, FX-C13-ESCAPE, FX-C13-MISSING, FX-C13-SYMLINK,
  FX-C13-DANGLING.

---

## 2A. Immutable-base schema inventory + coverage ledger (generated, not transcribed)

The classification below is the **complete** 351-column set **emitted by the
generator** from `Database._create_tables()` at the immutable delivery base —
**not** a hand-curated candidate list and **not** a name-pattern filter. The
per-consumer prose in §2 is a **compact cross-reference** to this ledger. Where
a §2 statement and this ledger disagree, **this ledger is authoritative**; a
consumer→ledger mismatch is a **default refusal** (§9), never a silent
omission.

**Why a filter is forbidden (binding).** The prior revision derived its
"candidate" set with a name-pattern filter (`…output…` / `…path…` / `…key…` /
`…dir…` / `…transcript…`). That filter silently omitted two columns that
**match** the pattern — `tasks.final_output_summary` and
`task_results.output_summary` — so the claimed "exhaustive" enumeration was not
exhaustive. The founder-approved rule (THR-187 seq 233–235) is **generation,
not transcription**: the inventory is emitted by a reproducible command, every
emitted column must be classified exactly once, an unmapped column fails, and
no human-authored candidate list may claim completeness. The reviewer checks
the generator and its output — never a hand-typed table.

**Finite Step-0 proof boundary (founder-resolved bounded re-scope, THR-187 seq
238–240).** The reviewer's request for individual source/call-site evidence for
each of the 331 `no-fs-consumer` rows is **not** an acceptance condition and is
**not** implemented here as an unbounded negative trace. Proving “no code path
resolves this column to filesystem bytes” per scalar/inline/ref/hash column is
a universal negative over the codebase; its only implementable form is a grep
for known resolver shapes — a pattern, the exact construct that failed prior
rounds. The ledger's taxonomy tags (`identity`, `inline`, `json`, `ref`,
`scalar`, `flag`, `integrity-hash`, …) are **classification buckets**, not
per-row source/call-site proof, and this document does **not** assert that
every `no-fs-consumer` column has an individually demonstrated negative source
trace.

The finite Step-0 proof boundary is instead: **(1)** trace every actual
DB→filesystem resolver/consumer and its producer (§2 — the 20 consumer
columns); **(2)** maintain the active-versus-dormant population proof (§2A — a
`dormant-legacy` field whose population proof is absent/nonzero fails closed;
an active currently-unpopulated column is recorded as an observation, not an
invariant); **(3)** default-refuse actual unresolved/out-of-policy file
references during export/import validation, before effects (every reference
classified file-bearing is resolved at export time and must land inside an
included, hash-validated root; anything else refuses); **(4)** STOP on a newly
discovered unclassified actual consumer (§9). The reproducible 351-column
generator + one-classification ledger remains the **exhaustiveness backstop** —
no column silently escapes the map — not a negative-trace proof. The named,
evidence-backed no-consumer categories are retained at this bounded policy
level only.

### Reproducible generator (command + recorded result)

**Recorded inputs.**
- Immutable delivery base: `9028700f9caeb5cb507e3600cd279dccd064000a`.
- Schema source: `runtime/infrastructure/database.py`
  `Database._create_tables()` — byte-identical at base / PR head / current main
  (verified `git diff --stat 9028700f 3728859c --
  runtime/infrastructure/database.py` → empty, and the same for base→main).
- Python 3.13 (stdlib `sqlite3`); no third-party dependency.

**Command** (run in the task worktree; emits the full inventory):

```python
import tempfile
from pathlib import Path
from runtime.infrastructure.database import Database

tmp = Path(tempfile.mkdtemp())
db = Database(tmp / "orgs" / "happyranch" / "happyranch.db")
conn = db._conn
tables = sorted(r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
print("TABLES", len(tables))
n = 0
for t in tables:
    for c in conn.execute(f"PRAGMA table_info({t})"):
        n += 1
        print(f"{t}\t{c['name']}\t{c['type']}")
print("COLUMNS", n)
```

**Recorded result: `TABLES 28`, `COLUMNS 351`.** The emitted inventory is the
351 tab-separated `table\tcolumn\ttype` data lines (the two `TABLES`/`COLUMNS`
summary lines excluded). Its SHA-256 is
`03456dcf426c74683df353c639984aa2ff8c8a61be88e1eaad065cc65d79f91f`. The
ledger below is generated from that same emission — its `table.column` and
`type` columns are the raw inventory, its `class`/`reconciliation` columns are
the classification — so a reviewer recomputes both from the command and diffs
them; nothing below is hand-transcribed.

### Completeness rule (replaces the prior name-pattern "candidate derivation")

There is **no filter**. A column is classified **because it is in the emitted
inventory**, not because its name matches a pattern. Every emitted column has
exactly one classification:

- **`consumer:<C-id>`** — the column resolves DB-held data to filesystem bytes
  and is mapped to a §2 consumer row (**20 columns**).
- **`no-fs-consumer:<tag>`** — the column does **not** resolve DB-held data to
  filesystem bytes (**331 columns**). `tag` ∈ `identity` (id / slug / skill_id /
  agent keys), `inline` (brief / summary / body / note / script_text / head /
  error strings), `json` (JSON-encoded structured field), `ref` (FK / id
  reference to another table), `scope-id` (`audit_log.task_id` prefixed scope
  id), `feishu` (dormant Feishu surface), `kb-slug` (filesystem-only KB slug —
  the KB tree is classified from disk, not from a DB path), `scalar` (count /
  int / real / timestamp), `flag` (status / boolean), `integrity-hash` (a
  content hash that references no path).
- **`dormant-legacy`** — a column of a legacy table not created by
  `_create_tables()` (see the legacy enumeration below; not part of the 351).
- **`default-refuse`** — **empty by construction**: the fallback bucket for any
  column the generator discovers that resolves DB-held data to filesystem bytes
  but has no §2 mapping. Any non-empty entry fires the named refusal
  (`unclassified_consumer`) before any capture/import effect (§2A harness).

**The two prior omissions are reconciled explicitly.** `tasks.final_output_summary`
(row 255) and `task_results.output_summary` (row 231) are **`no-fs-consumer:
inline`** — inline summary text with **no filesystem resolver**. Verified
readers only: `database.py:1441–1444` (fold `final_output_summary` → `note`),
`thread_store.py:122` and `run_step.py:2629` (display) for
`final_output_summary`; `pr_ci_merge.py:487–547`, `zombie_reaper.py:260`,
`routes/tasks.py`, `routes/threads.py`, `__main__.py:168` for `output_summary`.
They matched the old `…output…` pattern but are not consumers; under full
enumeration they are classified, not filtered.

The `no-fs-consumer:<tag>` tags are **classification buckets** (exhaustiveness
backstop), **not** per-row source/call-site proof: each tag records the column's
shape category, not an individually demonstrated negative resolver trace. The
finite proof boundary at the top of this section governs; a column may carry a
`no-fs-consumer` tag without an individually demonstrated negative trace.

### Coverage ledger (generated output — table | column | type | class | reconciliation)

| # | table.column | type | class | reconciliation |
| --- | --- | --- | --- | --- |
| 1 | `audit_log.id` | INTEGER | no-fs-consumer | `identity` |
| 2 | `audit_log.task_id` | TEXT | no-fs-consumer | `scope-id` |
| 3 | `audit_log.agent` | TEXT | no-fs-consumer | `identity` |
| 4 | `audit_log.action` | TEXT | no-fs-consumer | `inline` |
| 5 | `audit_log.payload` | TEXT | no-fs-consumer | `json` |
| 6 | `audit_log.timestamp` | TEXT | no-fs-consumer | `scalar` |
| 7 | `custom_skill_eligibility_events.id` | INTEGER | no-fs-consumer | `identity` |
| 8 | `custom_skill_eligibility_events.skill_id` | TEXT | no-fs-consumer | `identity` |
| 9 | `custom_skill_eligibility_events.actor` | TEXT | no-fs-consumer | `identity` |
| 10 | `custom_skill_eligibility_events.preview_revision` | INTEGER | no-fs-consumer | `scalar` |
| 11 | `custom_skill_eligibility_events.rule_set_json` | TEXT | no-fs-consumer | `json` |
| 12 | `custom_skill_eligibility_events.affected_newly_visible` | TEXT | no-fs-consumer | `json` |
| 13 | `custom_skill_eligibility_events.affected_newly_hidden` | TEXT | no-fs-consumer | `json` |
| 14 | `custom_skill_eligibility_events.created_at` | TEXT | no-fs-consumer | `scalar` |
| 15 | `custom_skill_eligibility_rules.id` | INTEGER | no-fs-consumer | `identity` |
| 16 | `custom_skill_eligibility_rules.skill_id` | TEXT | no-fs-consumer | `identity` |
| 17 | `custom_skill_eligibility_rules.scope_type` | TEXT | no-fs-consumer | `inline` |
| 18 | `custom_skill_eligibility_rules.scope_target` | TEXT | no-fs-consumer | `inline` |
| 19 | `custom_skill_eligibility_rules.effect` | TEXT | no-fs-consumer | `inline` |
| 20 | `custom_skill_eligibility_rules.created_at` | TEXT | no-fs-consumer | `scalar` |
| 21 | `custom_skill_eligibility_rules.created_by` | TEXT | no-fs-consumer | `identity` |
| 22 | `custom_skill_eligibility_rules.superseded_at` | TEXT | no-fs-consumer | `scalar` |
| 23 | `custom_skill_events.id` | INTEGER | no-fs-consumer | `identity` |
| 24 | `custom_skill_events.skill_id` | TEXT | no-fs-consumer | `identity` |
| 25 | `custom_skill_events.event_type` | TEXT | no-fs-consumer | `inline` |
| 26 | `custom_skill_events.actor` | TEXT | no-fs-consumer | `identity` |
| 27 | `custom_skill_events.version_id` | INTEGER | no-fs-consumer | `ref` |
| 28 | `custom_skill_events.metadata_json` | TEXT | no-fs-consumer | `json` |
| 29 | `custom_skill_events.created_at` | TEXT | no-fs-consumer | `scalar` |
| 30 | `custom_skill_events.task_id` | TEXT | no-fs-consumer | `ref` |
| 31 | `custom_skill_events.session_id` | TEXT | no-fs-consumer | `ref` |
| 32 | `custom_skill_materializations.id` | INTEGER | no-fs-consumer | `identity` |
| 33 | `custom_skill_materializations.skill_id` | TEXT | no-fs-consumer | `identity` |
| 34 | `custom_skill_materializations.agent_name` | TEXT | no-fs-consumer | `identity` |
| 35 | `custom_skill_materializations.task_id` | TEXT | no-fs-consumer | `ref` |
| 36 | `custom_skill_materializations.session_context` | TEXT | no-fs-consumer | `inline` |
| 37 | `custom_skill_materializations.session_id` | TEXT | no-fs-consumer | `ref` |
| 38 | `custom_skill_materializations.version_id` | INTEGER | no-fs-consumer | `ref` |
| 39 | `custom_skill_materializations.content_hash` | TEXT | no-fs-consumer | `integrity-hash` |
| 40 | `custom_skill_materializations.success` | INTEGER | no-fs-consumer | `flag` |
| 41 | `custom_skill_materializations.error_message` | TEXT | no-fs-consumer | `inline` |
| 42 | `custom_skill_materializations.created_at` | TEXT | no-fs-consumer | `scalar` |
| 43 | `custom_skill_versions.id` | INTEGER | no-fs-consumer | `identity` |
| 44 | `custom_skill_versions.skill_id` | TEXT | no-fs-consumer | `identity` |
| 45 | `custom_skill_versions.parent_version_id` | INTEGER | no-fs-consumer | `ref` |
| 46 | `custom_skill_versions.content_hash` | TEXT | **consumer** `C11b` | `C11b` |
| 47 | `custom_skill_versions.content_artifact_key` | TEXT | **consumer** `C11` | `C11` |
| 48 | `custom_skill_versions.skill_md_cache` | TEXT | **consumer** `C12` | `C12` |
| 49 | `custom_skill_versions.references_manifest` | TEXT | **consumer** `C12b` | `C12b` |
| 50 | `custom_skill_versions.assets_manifest` | TEXT | **consumer** `C12c` | `C12c` |
| 51 | `custom_skill_versions.validation_state` | TEXT | no-fs-consumer | `inline` |
| 52 | `custom_skill_versions.validator_version` | TEXT | no-fs-consumer | `inline` |
| 53 | `custom_skill_versions.validation_findings` | TEXT | no-fs-consumer | `inline` |
| 54 | `custom_skill_versions.created_at` | TEXT | no-fs-consumer | `scalar` |
| 55 | `custom_skill_versions.author_kind` | TEXT | no-fs-consumer | `inline` |
| 56 | `custom_skill_versions.author_identity` | TEXT | no-fs-consumer | `identity` |
| 57 | `custom_skill_versions.source_task_id` | TEXT | no-fs-consumer | `ref` |
| 58 | `custom_skill_versions.source_session_id` | TEXT | no-fs-consumer | `ref` |
| 59 | `custom_skill_versions.task_brief_digest` | TEXT | no-fs-consumer | `inline` |
| 60 | `custom_skills.id` | TEXT | no-fs-consumer | `identity` |
| 61 | `custom_skills.org_slug` | TEXT | no-fs-consumer | `identity` |
| 62 | `custom_skills.slug` | TEXT | no-fs-consumer | `identity` |
| 63 | `custom_skills.name` | TEXT | no-fs-consumer | `inline` |
| 64 | `custom_skills.description` | TEXT | no-fs-consumer | `inline` |
| 65 | `custom_skills.policy_class` | TEXT | no-fs-consumer | `inline` |
| 66 | `custom_skills.origin_kind` | TEXT | no-fs-consumer | `inline` |
| 67 | `custom_skills.origin_agent` | TEXT | no-fs-consumer | `identity` |
| 68 | `custom_skills.created_at` | TEXT | no-fs-consumer | `scalar` |
| 69 | `custom_skills.created_by` | TEXT | no-fs-consumer | `identity` |
| 70 | `custom_skills.current_version_id` | INTEGER | no-fs-consumer | `ref` |
| 71 | `custom_skills.retired_at` | TEXT | no-fs-consumer | `scalar` |
| 72 | `custom_skills.retired_by` | TEXT | no-fs-consumer | `identity` |
| 73 | `custom_skills.retired_reason` | TEXT | no-fs-consumer | `inline` |
| 74 | `dream_kb_candidates.id` | INTEGER | no-fs-consumer | `identity` |
| 75 | `dream_kb_candidates.dream_id` | TEXT | no-fs-consumer | `ref` |
| 76 | `dream_kb_candidates.agent_name` | TEXT | no-fs-consumer | `identity` |
| 77 | `dream_kb_candidates.slug` | TEXT | no-fs-consumer | `kb-slug` |
| 78 | `dream_kb_candidates.title` | TEXT | no-fs-consumer | `inline` |
| 79 | `dream_kb_candidates.topic` | TEXT | no-fs-consumer | `inline` |
| 80 | `dream_kb_candidates.rationale` | TEXT | no-fs-consumer | `inline` |
| 81 | `dream_kb_candidates.body_markdown` | TEXT | no-fs-consumer | `inline` |
| 82 | `dream_kb_candidates.status` | TEXT | no-fs-consumer | `flag` |
| 83 | `dream_kb_candidates.promoted_kb_slug` | TEXT | no-fs-consumer | `kb-slug` |
| 84 | `dream_kb_candidates.created_at` | TEXT | no-fs-consumer | `scalar` |
| 85 | `dream_kb_candidates.updated_at` | TEXT | no-fs-consumer | `scalar` |
| 86 | `dreams.id` | TEXT | no-fs-consumer | `identity` |
| 87 | `dreams.agent_name` | TEXT | no-fs-consumer | `identity` |
| 88 | `dreams.local_date` | TEXT | no-fs-consumer | `inline` |
| 89 | `dreams.scheduled_for` | TEXT | no-fs-consumer | `scalar` |
| 90 | `dreams.window_start` | TEXT | no-fs-consumer | `scalar` |
| 91 | `dreams.window_end` | TEXT | no-fs-consumer | `scalar` |
| 92 | `dreams.started_at` | TEXT | no-fs-consumer | `scalar` |
| 93 | `dreams.ended_at` | TEXT | no-fs-consumer | `scalar` |
| 94 | `dreams.status` | TEXT | no-fs-consumer | `flag` |
| 95 | `dreams.summary` | TEXT | no-fs-consumer | `inline` |
| 96 | `dreams.transcript_path` | TEXT | **consumer** `C6` | `C6` |
| 97 | `dreams.new_learnings_count` | INTEGER | no-fs-consumer | `scalar` |
| 98 | `dreams.kb_candidate_count` | INTEGER | no-fs-consumer | `scalar` |
| 99 | `dreams.founder_thread_id` | TEXT | no-fs-consumer | `ref` |
| 100 | `dreams.session_id` | TEXT | no-fs-consumer | `ref` |
| 101 | `dreams.error` | TEXT | no-fs-consumer | `inline` |
| 102 | `dreams.created_at` | TEXT | no-fs-consumer | `scalar` |
| 103 | `escalation_notifications.feishu_message_id` | TEXT | no-fs-consumer | `feishu` |
| 104 | `escalation_notifications.org_slug` | TEXT | no-fs-consumer | `feishu` |
| 105 | `escalation_notifications.task_id` | TEXT | no-fs-consumer | `feishu` |
| 106 | `escalation_notifications.chat_id` | TEXT | no-fs-consumer | `feishu` |
| 107 | `escalation_notifications.created_at` | TEXT | no-fs-consumer | `feishu` |
| 108 | `escalation_notifications.expires_at` | TEXT | no-fs-consumer | `feishu` |
| 109 | `escalation_notifications.consumed_at` | TEXT | no-fs-consumer | `feishu` |
| 110 | `escalation_notifications.consumed_by` | TEXT | no-fs-consumer | `feishu` |
| 111 | `escalation_notifications.kind` | TEXT | no-fs-consumer | `feishu` |
| 112 | `jobs.id` | TEXT | no-fs-consumer | `identity` |
| 113 | `jobs.task_id` | TEXT | no-fs-consumer | `ref` |
| 114 | `jobs.agent_name` | TEXT | no-fs-consumer | `identity` |
| 115 | `jobs.title` | TEXT | no-fs-consumer | `inline` |
| 116 | `jobs.rationale` | TEXT | no-fs-consumer | `inline` |
| 117 | `jobs.script_text` | TEXT | no-fs-consumer | `inline` |
| 118 | `jobs.interpreter` | TEXT | no-fs-consumer | `inline` |
| 119 | `jobs.cwd_hint` | TEXT | **consumer** `C5` | `C5` |
| 120 | `jobs.review_required` | INTEGER | no-fs-consumer | `flag` |
| 121 | `jobs.persistent` | INTEGER | no-fs-consumer | `flag` |
| 122 | `jobs.max_runtime_seconds` | INTEGER | no-fs-consumer | `scalar` |
| 123 | `jobs.max_output_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 124 | `jobs.status` | TEXT | no-fs-consumer | `flag` |
| 125 | `jobs.exit_code` | INTEGER | no-fs-consumer | `scalar` |
| 126 | `jobs.reason` | TEXT | no-fs-consumer | `inline` |
| 127 | `jobs.duration_ms` | INTEGER | no-fs-consumer | `scalar` |
| 128 | `jobs.stdout_head` | TEXT | no-fs-consumer | `inline` |
| 129 | `jobs.stderr_head` | TEXT | no-fs-consumer | `inline` |
| 130 | `jobs.stdout_path` | TEXT | **consumer** `C4` | `C4` |
| 131 | `jobs.stderr_path` | TEXT | **consumer** `C4` | `C4` |
| 132 | `jobs.stdout_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 133 | `jobs.stderr_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 134 | `jobs.cwd_resolved` | TEXT | **consumer** `C5b` | `C5b` |
| 135 | `jobs.started_at` | TEXT | no-fs-consumer | `scalar` |
| 136 | `jobs.finished_at` | TEXT | no-fs-consumer | `scalar` |
| 137 | `jobs.reviewed_at` | TEXT | no-fs-consumer | `scalar` |
| 138 | `jobs.reviewed_by` | TEXT | no-fs-consumer | `identity` |
| 139 | `jobs.reject_reason` | TEXT | no-fs-consumer | `inline` |
| 140 | `jobs.created_at` | TEXT | no-fs-consumer | `scalar` |
| 141 | `kb_views.slug` | TEXT | no-fs-consumer | `kb-slug` |
| 142 | `kb_views.view_count` | INTEGER | no-fs-consumer | `scalar` |
| 143 | `kb_views.last_viewed_at` | TEXT | no-fs-consumer | `scalar` |
| 144 | `manager_supersessions.id` | INTEGER | no-fs-consumer | `identity` |
| 145 | `manager_supersessions.predecessor_task_id` | TEXT | no-fs-consumer | `ref` |
| 146 | `manager_supersessions.successor_task_id` | TEXT | no-fs-consumer | `ref` |
| 147 | `manager_supersessions.original_root_task_id` | TEXT | no-fs-consumer | `ref` |
| 148 | `manager_supersessions.actor_agent` | TEXT | no-fs-consumer | `identity` |
| 149 | `manager_supersessions.actor_session_id` | TEXT | no-fs-consumer | `ref` |
| 150 | `manager_supersessions.rationale` | TEXT | no-fs-consumer | `inline` |
| 151 | `manager_supersessions.attestation_evidence` | TEXT | no-fs-consumer | `inline` |
| 152 | `manager_supersessions.predecessor_brief` | TEXT | no-fs-consumer | `inline` |
| 153 | `manager_supersessions.successor_brief` | TEXT | no-fs-consumer | `inline` |
| 154 | `manager_supersessions.predecessor_brief_sha256` | TEXT | no-fs-consumer | `integrity-hash` |
| 155 | `manager_supersessions.successor_brief_sha256` | TEXT | no-fs-consumer | `integrity-hash` |
| 156 | `manager_supersessions.created_at` | TEXT | no-fs-consumer | `scalar` |
| 157 | `org_settings.section` | TEXT | no-fs-consumer | `inline` |
| 158 | `org_settings.value_json` | TEXT | no-fs-consumer | `json` |
| 159 | `org_settings.updated_at` | TEXT | no-fs-consumer | `scalar` |
| 160 | `org_settings.updated_by` | TEXT | no-fs-consumer | `identity` |
| 161 | `processed_event_ids.org_slug` | TEXT | no-fs-consumer | `feishu` |
| 162 | `processed_event_ids.feishu_event_id` | TEXT | no-fs-consumer | `feishu` |
| 163 | `processed_event_ids.processed_at` | TEXT | no-fs-consumer | `feishu` |
| 164 | `processed_event_ids.outcome` | TEXT | no-fs-consumer | `feishu` |
| 165 | `processed_event_ids.reason` | TEXT | no-fs-consumer | `feishu` |
| 166 | `schedules.id` | TEXT | no-fs-consumer | `identity` |
| 167 | `schedules.agent_name` | TEXT | no-fs-consumer | `identity` |
| 168 | `schedules.team` | TEXT | no-fs-consumer | `inline` |
| 169 | `schedules.kind` | TEXT | no-fs-consumer | `inline` |
| 170 | `schedules.fire_at` | TEXT | no-fs-consumer | `scalar` |
| 171 | `schedules.recurrence` | TEXT | no-fs-consumer | `inline` |
| 172 | `schedules.timezone` | TEXT | no-fs-consumer | `inline` |
| 173 | `schedules.normalized_brief` | TEXT | no-fs-consumer | `inline` |
| 174 | `schedules.source_instruction` | TEXT | no-fs-consumer | `inline` |
| 175 | `schedules.status` | TEXT | no-fs-consumer | `flag` |
| 176 | `schedules.active` | INTEGER | no-fs-consumer | `flag` |
| 177 | `schedules.expires_at` | TEXT | no-fs-consumer | `scalar` |
| 178 | `schedules.indefinite` | INTEGER | no-fs-consumer | `flag` |
| 179 | `schedules.spawned_task_ids` | TEXT | no-fs-consumer | `json` |
| 180 | `schedules.last_fired_at` | TEXT | no-fs-consumer | `scalar` |
| 181 | `schedules.fire_count` | INTEGER | no-fs-consumer | `scalar` |
| 182 | `schedules.session_id` | TEXT | no-fs-consumer | `ref` |
| 183 | `schedules.error` | TEXT | no-fs-consumer | `inline` |
| 184 | `schedules.transcript_path` | TEXT | **consumer** `C8` | `C8` |
| 185 | `schedules.created_at` | TEXT | no-fs-consumer | `scalar` |
| 186 | `schedules.updated_at` | TEXT | no-fs-consumer | `scalar` |
| 187 | `schedules.end_reason` | TEXT | no-fs-consumer | `inline` |
| 188 | `session_token_usage.id` | INTEGER | no-fs-consumer | `identity` |
| 189 | `session_token_usage.task_id` | TEXT | no-fs-consumer | `ref` |
| 190 | `session_token_usage.agent` | TEXT | no-fs-consumer | `identity` |
| 191 | `session_token_usage.session_id` | TEXT | no-fs-consumer | `ref` |
| 192 | `session_token_usage.executor` | TEXT | no-fs-consumer | `inline` |
| 193 | `session_token_usage.model` | TEXT | no-fs-consumer | `inline` |
| 194 | `session_token_usage.input_tokens` | INTEGER | no-fs-consumer | `scalar` |
| 195 | `session_token_usage.output_tokens` | INTEGER | no-fs-consumer | `scalar` |
| 196 | `session_token_usage.cache_read_tokens` | INTEGER | no-fs-consumer | `scalar` |
| 197 | `session_token_usage.cache_creation_tokens` | INTEGER | no-fs-consumer | `scalar` |
| 198 | `session_token_usage.reasoning_tokens` | INTEGER | no-fs-consumer | `scalar` |
| 199 | `session_token_usage.usage_raw_json` | TEXT | no-fs-consumer | `json` |
| 200 | `session_token_usage.scope_type` | TEXT | no-fs-consumer | `inline` |
| 201 | `session_token_usage.scope_id` | TEXT | no-fs-consumer | `inline` |
| 202 | `session_token_usage.thread_id` | TEXT | no-fs-consumer | `ref` |
| 203 | `session_token_usage.invocation_purpose` | TEXT | no-fs-consumer | `inline` |
| 204 | `session_token_usage.created_at` | TEXT | no-fs-consumer | `scalar` |
| 205 | `skill_validation_events.id` | INTEGER | no-fs-consumer | `identity` |
| 206 | `skill_validation_events.skill_id` | TEXT | no-fs-consumer | `identity` |
| 207 | `skill_validation_events.slug` | TEXT | no-fs-consumer | `identity` |
| 208 | `skill_validation_events.agent` | TEXT | no-fs-consumer | `identity` |
| 209 | `skill_validation_events.source` | TEXT | no-fs-consumer | `inline` |
| 210 | `skill_validation_events.severity` | TEXT | no-fs-consumer | `inline` |
| 211 | `skill_validation_events.ok` | INTEGER | no-fs-consumer | `flag` |
| 212 | `skill_validation_events.version` | TEXT | no-fs-consumer | `inline` |
| 213 | `skill_validation_events.findings` | TEXT | no-fs-consumer | `inline` |
| 214 | `skill_validation_events.reason_codes` | TEXT | no-fs-consumer | `inline` |
| 215 | `skill_validation_events.created_at` | TEXT | no-fs-consumer | `scalar` |
| 216 | `task_attachments.id` | INTEGER | no-fs-consumer | `identity` |
| 217 | `task_attachments.task_id` | TEXT | no-fs-consumer | `ref` |
| 218 | `task_attachments.ordinal` | INTEGER | no-fs-consumer | `scalar` |
| 219 | `task_attachments.storage_key` | TEXT | **consumer** `C1` | `C1` |
| 220 | `task_attachments.display_name` | TEXT | no-fs-consumer | `inline` |
| 221 | `task_attachments.size_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 222 | `task_attachments.content_type` | TEXT | no-fs-consumer | `inline` |
| 223 | `task_attachments.uploaded_by` | TEXT | no-fs-consumer | `identity` |
| 224 | `task_attachments.created_at` | TEXT | no-fs-consumer | `scalar` |
| 225 | `task_attachments.legacy_status` | TEXT | no-fs-consumer | `inline` |
| 226 | `task_results.id` | INTEGER | no-fs-consumer | `identity` |
| 227 | `task_results.task_id` | TEXT | no-fs-consumer | `ref` |
| 228 | `task_results.agent` | TEXT | no-fs-consumer | `identity` |
| 229 | `task_results.session_id` | TEXT | no-fs-consumer | `ref` |
| 230 | `task_results.status` | TEXT | no-fs-consumer | `flag` |
| 231 | `task_results.output_summary` | TEXT | no-fs-consumer | `inline` |
| 232 | `task_results.decision_json` | TEXT | no-fs-consumer | `json` |
| 233 | `task_results.confidence_score` | INTEGER | no-fs-consumer | `scalar` |
| 234 | `task_results.learnings` | TEXT | no-fs-consumer | `inline` |
| 235 | `task_results.risks_flagged` | TEXT | no-fs-consumer | `inline` |
| 236 | `task_results.duration_seconds` | INTEGER | no-fs-consumer | `scalar` |
| 237 | `task_results.token_count` | INTEGER | no-fs-consumer | `scalar` |
| 238 | `task_results.estimated_cost` | REAL | no-fs-consumer | `scalar` |
| 239 | `task_results.output_dir` | TEXT | **consumer** `C10b` | `C10b` |
| 240 | `task_results.created_at` | TEXT | no-fs-consumer | `scalar` |
| 241 | `task_results.waiting_on_job_ids` | TEXT | no-fs-consumer | `json` |
| 242 | `task_results.verdict` | TEXT | no-fs-consumer | `inline` |
| 243 | `task_results.local_ci` | TEXT | no-fs-consumer | `inline` |
| 244 | `tasks.id` | TEXT | no-fs-consumer | `identity` |
| 245 | `tasks.status` | TEXT | no-fs-consumer | `flag` |
| 246 | `tasks.assigned_agent` | TEXT | no-fs-consumer | `identity` |
| 247 | `tasks.team` | TEXT | no-fs-consumer | `inline` |
| 248 | `tasks.brief` | TEXT | no-fs-consumer | `inline` |
| 249 | `tasks.task_type` | TEXT | no-fs-consumer | `inline` |
| 250 | `tasks.revision_count` | INTEGER | no-fs-consumer | `scalar` |
| 251 | `tasks.created_at` | TEXT | no-fs-consumer | `scalar` |
| 252 | `tasks.updated_at` | TEXT | no-fs-consumer | `scalar` |
| 253 | `tasks.completed_at` | TEXT | no-fs-consumer | `scalar` |
| 254 | `tasks.parent_task_id` | TEXT | no-fs-consumer | `ref` |
| 255 | `tasks.final_output_summary` | TEXT | no-fs-consumer | `inline` |
| 256 | `tasks.final_output_dir` | TEXT | **consumer** `C10` | `C10` |
| 257 | `tasks.executor_pid` | INTEGER | no-fs-consumer | `scalar` |
| 258 | `tasks.block_kind` | TEXT | no-fs-consumer | `inline` |
| 259 | `tasks.note` | TEXT | no-fs-consumer | `inline` |
| 260 | `tasks.orchestration_step_count` | INTEGER | no-fs-consumer | `scalar` |
| 261 | `tasks.cancelled_at` | TEXT | no-fs-consumer | `scalar` |
| 262 | `tasks.revisit_of_task_id` | TEXT | no-fs-consumer | `ref` |
| 263 | `tasks.last_heartbeat` | TEXT | no-fs-consumer | `scalar` |
| 264 | `tasks.session_timeout_seconds` | INTEGER | no-fs-consumer | `scalar` |
| 265 | `tasks.blocked_on_job_ids` | TEXT | no-fs-consumer | `json` |
| 266 | `tasks.dispatched_from_thread_id` | TEXT | no-fs-consumer | `ref` |
| 267 | `tasks.active_chain` | TEXT | no-fs-consumer | `json` |
| 268 | `tasks.active_fanout` | TEXT | no-fs-consumer | `json` |
| 269 | `tasks.current_session_id` | TEXT | no-fs-consumer | `ref` |
| 270 | `tasks.zombie_flagged_at` | TEXT | no-fs-consumer | `scalar` |
| 271 | `thread_invocations.id` | INTEGER | no-fs-consumer | `identity` |
| 272 | `thread_invocations.thread_id` | TEXT | no-fs-consumer | `ref` |
| 273 | `thread_invocations.agent_name` | TEXT | no-fs-consumer | `identity` |
| 274 | `thread_invocations.invocation_token` | TEXT | no-fs-consumer | `inline` |
| 275 | `thread_invocations.triggering_seq` | INTEGER | no-fs-consumer | `scalar` |
| 276 | `thread_invocations.purpose` | TEXT | no-fs-consumer | `inline` |
| 277 | `thread_invocations.status` | TEXT | no-fs-consumer | `flag` |
| 278 | `thread_invocations.enqueued_at` | TEXT | no-fs-consumer | `scalar` |
| 279 | `thread_invocations.started_at` | TEXT | no-fs-consumer | `scalar` |
| 280 | `thread_invocations.consumed_at` | TEXT | no-fs-consumer | `scalar` |
| 281 | `thread_invocations.session_id` | TEXT | no-fs-consumer | `ref` |
| 282 | `thread_invocations.dispatched_task_id` | TEXT | no-fs-consumer | `ref` |
| 283 | `thread_invocations.decline_reason` | TEXT | no-fs-consumer | `inline` |
| 284 | `thread_message_attachments.id` | INTEGER | no-fs-consumer | `identity` |
| 285 | `thread_message_attachments.thread_id` | TEXT | no-fs-consumer | `ref` |
| 286 | `thread_message_attachments.message_seq` | INTEGER | no-fs-consumer | `scalar` |
| 287 | `thread_message_attachments.ordinal` | INTEGER | no-fs-consumer | `scalar` |
| 288 | `thread_message_attachments.artifact_name` | TEXT | **consumer** `C2` | `C2` |
| 289 | `thread_message_attachments.display_name` | TEXT | no-fs-consumer | `inline` |
| 290 | `thread_message_attachments.size_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 291 | `thread_message_attachments.content_type` | TEXT | no-fs-consumer | `inline` |
| 292 | `thread_message_attachments.uploaded_by` | TEXT | no-fs-consumer | `identity` |
| 293 | `thread_message_attachments.created_at` | TEXT | no-fs-consumer | `scalar` |
| 294 | `thread_message_attachments.thread_attachment_id` | TEXT | **consumer** `C2b` | `C2b` |
| 295 | `thread_messages.id` | INTEGER | no-fs-consumer | `identity` |
| 296 | `thread_messages.thread_id` | TEXT | no-fs-consumer | `ref` |
| 297 | `thread_messages.seq` | INTEGER | no-fs-consumer | `scalar` |
| 298 | `thread_messages.speaker` | TEXT | no-fs-consumer | `identity` |
| 299 | `thread_messages.kind` | TEXT | no-fs-consumer | `inline` |
| 300 | `thread_messages.body_markdown` | TEXT | no-fs-consumer | `inline` |
| 301 | `thread_messages.addressed_to_json` | TEXT | no-fs-consumer | `json` |
| 302 | `thread_messages.decline_reason` | TEXT | no-fs-consumer | `inline` |
| 303 | `thread_messages.system_payload_json` | TEXT | no-fs-consumer | `json` |
| 304 | `thread_messages.sent_from_task_id` | TEXT | no-fs-consumer | `ref` |
| 305 | `thread_messages.created_at` | TEXT | no-fs-consumer | `scalar` |
| 306 | `thread_participants.thread_id` | TEXT | no-fs-consumer | `ref` |
| 307 | `thread_participants.agent_name` | TEXT | no-fs-consumer | `identity` |
| 308 | `thread_participants.added_at` | TEXT | no-fs-consumer | `scalar` |
| 309 | `thread_participants.added_by` | TEXT | no-fs-consumer | `identity` |
| 310 | `thread_participants.agent_session_id` | TEXT | no-fs-consumer | `ref` |
| 311 | `thread_participants.last_resumed_seq` | INTEGER | no-fs-consumer | `scalar` |
| 312 | `thread_scoped_attachments.id` | INTEGER | no-fs-consumer | `identity` |
| 313 | `thread_scoped_attachments.attachment_id` | TEXT | **consumer** `C3` | `C3` |
| 314 | `thread_scoped_attachments.thread_id` | TEXT | **consumer** `C3` | `C3` |
| 315 | `thread_scoped_attachments.display_name` | TEXT | no-fs-consumer | `inline` |
| 316 | `thread_scoped_attachments.size_bytes` | INTEGER | no-fs-consumer | `scalar` |
| 317 | `thread_scoped_attachments.content_type` | TEXT | no-fs-consumer | `inline` |
| 318 | `thread_scoped_attachments.uploaded_by` | TEXT | no-fs-consumer | `identity` |
| 319 | `thread_scoped_attachments.created_at` | TEXT | no-fs-consumer | `scalar` |
| 320 | `threads.id` | TEXT | no-fs-consumer | `identity` |
| 321 | `threads.subject` | TEXT | no-fs-consumer | `inline` |
| 322 | `threads.started_at` | TEXT | no-fs-consumer | `scalar` |
| 323 | `threads.archived_at` | TEXT | no-fs-consumer | `scalar` |
| 324 | `threads.status` | TEXT | no-fs-consumer | `flag` |
| 325 | `threads.forwarded_from_id` | TEXT | no-fs-consumer | `ref` |
| 326 | `threads.forwarded_from_kind` | TEXT | no-fs-consumer | `inline` |
| 327 | `threads.turn_cap` | INTEGER | no-fs-consumer | `scalar` |
| 328 | `threads.turns_used` | INTEGER | no-fs-consumer | `scalar` |
| 329 | `threads.summary` | TEXT | no-fs-consumer | `inline` |
| 330 | `threads.transcript_path` | TEXT | **consumer** `C7` | `C7` |
| 331 | `threads.composed_by` | TEXT | no-fs-consumer | `identity` |
| 332 | `threads.composed_from_task_id` | TEXT | no-fs-consumer | `ref` |
| 333 | `threads.composed_from_dream_id` | TEXT | no-fs-consumer | `ref` |
| 334 | `work_hours.id` | TEXT | no-fs-consumer | `identity` |
| 335 | `work_hours.agent_name` | TEXT | no-fs-consumer | `identity` |
| 336 | `work_hours.local_date` | TEXT | no-fs-consumer | `inline` |
| 337 | `work_hours.slot` | TEXT | no-fs-consumer | `inline` |
| 338 | `work_hours.mode` | TEXT | no-fs-consumer | `inline` |
| 339 | `work_hours.scheduled_for` | TEXT | no-fs-consumer | `scalar` |
| 340 | `work_hours.started_at` | TEXT | no-fs-consumer | `scalar` |
| 341 | `work_hours.ended_at` | TEXT | no-fs-consumer | `scalar` |
| 342 | `work_hours.status` | TEXT | no-fs-consumer | `flag` |
| 343 | `work_hours.routine_count` | INTEGER | no-fs-consumer | `scalar` |
| 344 | `work_hours.dropped_count` | INTEGER | no-fs-consumer | `scalar` |
| 345 | `work_hours.spawned_task_ids` | TEXT | no-fs-consumer | `json` |
| 346 | `work_hours.spawned_task_count` | INTEGER | no-fs-consumer | `scalar` |
| 347 | `work_hours.summary` | TEXT | no-fs-consumer | `inline` |
| 348 | `work_hours.transcript_path` | TEXT | **consumer** `C9` | `C9` |
| 349 | `work_hours.session_id` | TEXT | no-fs-consumer | `ref` |
| 350 | `work_hours.error` | TEXT | no-fs-consumer | `inline` |
| 351 | `work_hours.created_at` | TEXT | no-fs-consumer | `scalar` |
**Counts:** 20 `consumer` columns (mapped to C1–C13), 331 `no-fs-consumer`
columns — **351 total, every emitted column classified exactly once**. The
generator's self-check asserted `tables == 28`, `columns == 351`, no unmapped
emitted column, no phantom mapping key, and no blank/unknown classification —
all passed. This is the auditable completeness proof; it is produced by running
the command, not by hand.

### Legacy-table enumeration (dormant-legacy — not in the 351-column fresh schema)

A pre-retirement DB contains tables beyond the 28 `_create_tables()` tables.
They are **not** in the 351-column emission (the fresh generator has already
dropped/renamed them), so the harness enumerates them **separately** by a
read-only `sqlite_master` inspection — performed **after** the clean DB-parent
sidecar scan and **still strictly before** any `Database(...)` construction
(see §5/§6) — and classifies each column `dormant-legacy`:

| Legacy table | Present in live DB | Disposition | Column(s) of concern |
| --- | --- | --- | --- |
| `agent_enrollments` | yes (0 rows, 9 cols, verified live) | `dormant-legacy`; refuse-if-populated | `repos` (TEXT) is the only path-shaped column → **D1** (no resolver); the other 8 are identity/inline |
| `skill_lifecycle_packages` + 3 sibling `skill_lifecycle_%` tables | no (retired at `Database.__init__`, database.py:199–240) | `dormant-legacy`; **destructive consumer** | `content_artifact_key` → **C13** (`ArtifactStore.delete`, database.py:235–239) |
| `talks` (+ talk id columns on tasks/jobs/threads/session_token_usage) | no (dropped by `_migrate_drop_talk_surface_if_needed`, database.py:353–433) | `dormant-legacy`; dropped | talk id columns — no filesystem resolver |
| `script_requests` | no (renamed to `jobs` by `_migrate_jobs_table_if_needed`, database.py:242–340) | `dormant-legacy`; renamed | path columns map to C4/C5 (`stdout_path` / `stderr_path` / `cwd_hint`) |

**Dormant population proof (binding, dormant-legacy only).** A field explicitly
classified `dormant-legacy` (a legacy table not created by `_create_tables()`)
carries a **live-population proof of empty** (recorded read-only on the live
org DB): `agent_enrollments.repos` = 0 rows. A `dormant-legacy` classification
whose live-population proof is **absent or nonzero** fails the gate (§2A
harness) — the refusal fires **before any capture/import effect**.

**Active-versus-dormant distinction (binding).**
`custom_skill_versions.references_manifest` and `assets_manifest` are
**active** current persisted metadata columns (declared in `_create_tables()`,
`database.py:1090–1091`), **not** dormant legacy fields. Their current
zero-population is an **observation**, not a dormant-emptiness invariant: a
non-null active value does **not** trip the dormant-empty control merely
because the column exists. If they ever become populated, the embedded
references (if any) are subject to the export-time default refusal (§2A / §6),
**not** to a dormant-stays-empty rule.

### Harness/check definition (the controls must fire)

The generator's self-check is the harness definition; the Slice B/C harness must
additionally prove **each control fires** (not merely describe it). The check
**fails** (non-zero, gate red) on:

1. **Inventory-count/key mismatch** — `tables != 28`, `columns != 351`, or any
   table/column name differing from the emitted set.
2. **Duplicate mapping** — a classification-map key not present in the emitted
   schema (phantom key).
3. **Blank/unmapped classification** — an emitted column absent from the
   classification map.
4. **Unclassified discovered DB→file consumer** — any column whose
   classification resolves to `default-refuse` (a column that resolves to
   filesystem bytes but has no §2 C-id). **Firing proof:** inject a 14th
   path-bearing column (or populate a `dormant-legacy` path column) and assert
   the check fails. **Default-refuse fires before any capture/import effect** —
   never a silent portable classification.
5. **Dormant-legacy classification whose live-population proof is absent or
   nonzero** — populate a column classified `dormant-legacy` (e.g. an
   `agent_enrollments.repos` row in a legacy DB) and assert the check fails,
   forcing reclassification. **Default-refuse fires before any capture/import
   effect.** Active columns with currently-zero population (e.g.
   `references_manifest` / `assets_manifest`) are **not** subject to this
   control: a non-null active value is handled by the export-time default
   refusal (§2A / §6), not a dormant-emptiness assertion.

The §4 harness requirements add the two behavioral firing proofs (an unsafe
physical object → named refusal with **zero** source connections and no archive;
bijective fixture cross-reference). The §2A default-refuse matrix below is the
authoritative `(now)` vs `(req)` statement.

### Default-refuse policy per consumer × physical state

Legend — each cell states the **disposition** for that state, marked `(now)`
when today's resolver enforces it and `(req)` when it is a **required future
pre-effect refusal** (today's resolver does **not** enforce it and must not be
described as if it did):

- **safe regular approved member** — the only portable state.
- **missing** — named refusal or empty/re-derived, per consumer.
- **outside-root escape** — `refuse(escape)`.
- **in-root symlink** — `refuse(nonregular)`.
- **dangling link** — `refuse(dangling)`.
- **nonregular (dir/device/FIFO)** — `refuse(nonregular)`.
- **malformed/staged-invalid identifier/path** — `refuse(invalid)`.
- **hash/integrity** — `refuse(integrity)` (C11 only).

| Consumer | safe regular | missing | outside-root | in-root symlink | dangling | nonregular | malformed/invalid | hash/integrity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C1 (task-attachments) | include | `missing`(now) | `escape`(now) | `nonregular`(req; admits now) | `dangling`(req; `read` 404 now) | `nonregular`(req) | `invalid`(now: token regex) | n/a |
| C2 (artifacts) | include | `missing`(now 404) | `escape`(now) | `nonregular`(req; follows now) | `dangling`(req; `exists()` false now) | `nonregular`(req; dir→404 now) | `invalid`(now) | n/a |
| C3 (thread-scoped) | include | `missing`(now KeyError) | `escape`(req; none now) | `nonregular`(req) | `dangling`(req) | `nonregular`(req) | `invalid`(req; none now) | n/a |
| C4 (jobs stdout/stderr) | target-local | empty stream (now) | `escape`(req; abs open now) | `nonregular`(req) | `dangling`(req) | `nonregular`(req) | `invalid`(req) | n/a |
| C5 (cwd_hint) | exclude bytes; string include | `cwd_missing`(now at run) | `escape`(req; no containment now) | `nonregular`(req) | `dangling`(req) | `nonregular`(req) | `invalid`(req; nested-relative ok) | n/a |
| C6–C9 (transcripts) | include (re-derived) | re-derived (now) | n/a (stale abs, not resolved) | n/a | n/a | n/a | n/a | n/a |
| C10 (output_dir) | exclude | dangling relative (now) | `escape`(now: containment→None) | n/a (excluded) | n/a | n/a | n/a | n/a |
| C11 (content_artifact_key) | include | `missing`(now) | `escape`(now) | `nonregular`(req; follows now) | `dangling`(req; `exists()` false now) | `nonregular`(req) | `invalid`(now) | `integrity`(now) |
| C13 (legacy lifecycle) | reject-until-retired | `ArtifactNotFound` swallowed (now); detector refuses (req) | `escape`(now crash vector; detector refuses req) | unlink link only, non-dangling (now); refuse (req) | `ArtifactNotFound` swallowed, link left (now); refuse (req) | `ArtifactNotFound` dir (now); refuse (req) | `invalid`(now crash; detector refuses req) | n/a |

**States not supported by today's resolver are marked `(req)` and are required
future pre-effect refusals** — never inferred current behavior. The §2 prose for
each consumer repeats the `(now)` vs `(req)` split; this matrix is the
authoritative reconciliation.

---

## 3. "No consumer found" — tables/fields inspected without a filesystem resolver

The following were inspected and **do not** resolve DB-held data to filesystem
bytes (or the reference is a slug/id, not a path). They are documented to
prevent a future implementer from assuming a path dependency exists. This table
states the **bounded policy-level** no-consumer categories (with the evidence
behind each); it is **not** a claim that every `no-fs-consumer` column has an
individually demonstrated negative resolver trace — that obligation is out of
Step-0 scope per the finite proof boundary in §2A.

> **Authoritative enumeration is §2A.** This table is a *prose summary* of the
> `no-fs-consumer` / `dormant-legacy` classifications; the complete 351-column
> coverage ledger (§2A) is the source of truth, and every column there is
> classified exactly once. Any column this table omits is still classified in
> §2A — there is no hand-curated list that may claim completeness.

| Table / field | Why no filesystem resolver |
| --- | --- |
| `kb_views.slug`, `dream_kb_candidates.promoted_kb_slug` | KB is filesystem-only; the DB holds **slugs**, resolved by `kb_store.path_for(slug)` at load, not stored paths. KB files under `kb/` are allow-listed but are not *referenced by* a DB path column. |
| `skill_validation_events.skill_id` / `slug` | Legacy-skill validation rows key on id/slug; no path column. The legacy `skills/<pkg>/` tree is classified by `_classify_skills` from the filesystem, not from a DB path. |
| `audit_log` (all columns) | `task_id` is a scope id (with `artifact:`/`config:`/`TASK-` prefixes), not a path. |
| `org_settings.value_json` | JSON settings (dreaming, threads, session_timeout, working_hours). No file-path values. |
| `escalation_notifications` / `processed_event_ids` | Dormant Feishu tables (THR-022 removed); no filesystem reference. |
| `session_token_usage`, `tasks.brief`, `thread_messages.body_markdown` | Inline text. |
| `tasks.final_output_summary`, `task_results.output_summary` | Inline summary text with **no filesystem resolver** — explicitly reconciled in §2A (rows 255 / 231) as `no-fs-consumer:inline`. Readers only: `database.py:1441–1444` (fold `final_output_summary` → `note`), `thread_store.py:122`, `run_step.py:2629`; `pr_ci_merge.py:487–547`, `zombie_reaper.py:260`, `routes/tasks.py`, `routes/threads.py`, `__main__.py:168`. Previously omitted by the name-pattern filter; now classified, not filtered. |
| `thread_message_attachments.thread_attachment_id` | Legacy `att-NNN` identifier; used only as a display fallback (`threads.py:541,896`). Not a path column. |
| `custom_skills.slug` / `custom_skills.org_slug` | Skill identity; the filesystem path is materialized into `custom_skill_versions.content_artifact_key` (C11), not resolved from these columns. |
| `agent_enrollments.*` (legacy table, 0 rows; not in current schema) | Retired table (dropped from `_create_tables`; no `runtime/` reference). Its `repos` TEXT column has **no resolver** on current main. A legacy DB with a populated `agent_enrollments.repos` is an unclassified candidate ⇒ default refusal (see §2A and §4 harness requirement (ii)). |
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
| FX-C2-ESCAPE | C2 | `artifact_name="../../etc/passwd"` | — | escaped-path | `escape` (validate_name rejects `..`) | no | no |
| FX-C2-SYMLINK | C2 | `artifact_name="reports/x"` | `artifacts/reports/x → /etc/passwd` (outside root) | symlink (resolved target escapes root) | `escape` (`path_for` containment rejects resolved target) | no | no |
| FX-C2-INROOT-SYMLINK | C2 | `artifact_name="reports/x"` | `artifacts/reports/x → artifacts/reports/y` (in-root) | in-root symlink | `nonregular` — current code **admits** (containment passes, `stat()` follows); required capture/import must refuse fail-closed | no | no |
| FX-C2-NONREGULAR | C2 | `artifact_name="reports/fifo"` | `artifacts/reports/fifo` (FIFO/special) | nonregular member | `nonregular` — required capture/import must refuse | no | no |
| FX-C3-OK | C3 | `thread_scoped_attachments(attachment_id="att-1", thread_id="THR-1")` | `threads/THR-1/attachments/att-1` | — | none (included) | no | no |
| FX-C3-MISSING | C3 | same row | file absent | missing-file | `missing` | no | no |
| FX-C3-ESCAPE | C3 | `thread_scoped_attachments(thread_id="../..", attachment_id="att-1")` | — | staged-DB escape identifier | `escape` — no validation today (would resolve outside root); required segment validation refuses | no | no |
| FX-C3-SYMLINK | C3 | same row, `attachment_id="att-1"` | `threads/THR-1/attachments/att-1 → outside` | symlink/nonregular | `nonregular` — required capture/import refuses | no | no |
| FX-C3-NONREGULAR | C3 | same row | `threads/THR-1/attachments/att-1` (FIFO/special) | nonregular member | `nonregular` — required capture/import refuses | no | no |
| FX-C4-ABS | C4 | `jobs(id="JOB-9", stdout_path="<src>/jobs/JOB-9.out", stderr_path="<src>/jobs/JOB-9.err")` | `jobs/JOB-9.out`, `jobs/JOB-9.err` | **absolute path re-resolution** | import-time rebase (target-local) — see §9 | read-only | yes (rebase) |
| FX-C4-MISSING | C4 | same row | files absent | missing-file | `missing` (empty stream) | no | no |
| FX-C5-REL | C5 | `jobs(cwd_hint="subdir")` | — | valid relative (submit path) | none (excluded bytes; resolves under `workspace_root`) | no | no |
| FX-C5-NESTED | C5 | `jobs(cwd_hint="repos/web-app")` (submit path) | — | valid **nested** relative (submit path) | none — route-compatible nested-relative resolves under `workspace_root`; bytes excluded | no | no |
| FX-C5-ABS | C5 | `jobs(cwd_hint="/etc")` (staged DB, bypasses submit validation) | — | staged-DB absolute | `escape` — `_resolve_cwd` resolves outside `workspace_root`; required staged-DB validation refuses, no effect | no | no |
| FX-C5-DOTDOT | C5 | `jobs(cwd_hint="../../outside")` (staged DB) | — | staged-DB dotdot | `escape` — `_resolve_cwd` resolves above `workspace_root`; required staged-DB validation refuses, no effect | no | no |
| FX-C6-ABS | C6 | `dreams(id="DREAM-1", transcript_path="<src>/dreams/DREAM-1.md")` | `dreams/DREAM-1.md` | stale absolute (display-only) | none — re-derived from `dream_id` | read-only | no |
| FX-C7-ABS | C7 | `threads(id="THR-1", transcript_path="<src>/threads/THR-1.md")` | `threads/THR-1.md` | stale absolute (derived) | none — regenerable | read-only | no |
| FX-C8-ABS | C8 | `schedules(id="SCHEDULE-1", transcript_path="<src>/schedules/SCHEDULE-1.md")` | `schedules/SCHEDULE-1.md` | stale absolute (display-only) | none | read-only | no |
| FX-C9-ABS | C9 | `work_hours(id="WORKHOUR-1", transcript_path="<src>/work_hours/WORKHOUR-1.md")` | `work_hours/WORKHOUR-1.md` | stale absolute (display-only) | none | read-only | no |
| FX-C10-REL | C10 | `tasks(id="T-1", final_output_dir="output/T-1")` | `workspaces/a/output/T-1/*` | excluded workspace output | `missing`-after-import (dangling relative ref) | no | no |
| FX-C11-OK | C11 | `custom_skill_versions(content_artifact_key="custom-skills/s/digest/SKILL.md", content_hash=<sha256>)` | `artifacts/custom-skills/s/digest/SKILL.md` matching hash (regular file, no symlink) | — | none (included; hash verified) | read-only | no |
| FX-C11-HASHMISMATCH | C11 | same row, `content_hash=<wrong>` | file bytes differ | hash/content mismatch | `integrity` (refusal) | no | no |
| FX-C11-MISSING | C11 | same row | file absent | missing-file | `missing` (refusal) | no | no |
| FX-C11-OUTROOT-SYMLINK | C11 | same row | `artifacts/custom-skills/s/digest/SKILL.md → /etc/passwd` (outside root) | symlink (resolved target escapes root) | `escape` (`path_for` containment rejects resolved target) | no | no |
| FX-C11-INROOT-SYMLINK | C11 | same row | `artifacts/custom-skills/s/digest/SKILL.md → artifacts/other` (in-root) | in-root symlink | `nonregular` — current `read()` **follows** and bytes may hash-match `content_hash`; required recursive validation refuses fail-closed | no | no |
| FX-C11-NONREGULAR | C11 | same row | `artifacts/custom-skills/s/digest/SKILL.md` (FIFO/special) | nonregular member | `nonregular` — required recursive validation refuses | no | no |
| FX-C12-INLINE | C12 | `custom_skill_versions(skill_md_cache="…", references_manifest=null, assets_manifest=null)` | none | inline only | none (no fs ref) | no | no |
| FX-C13-ABSENT | C13 | no `skill_lifecycle_%` table (current-v2 DB) | none | — | none (no consumer; proceed) | no | no |
| FX-C13-ESCAPE | C13 | `skill_lifecycle_packages(content_artifact_key="../../etc/passwd")` | — | invalid/escape key | `escape` — read-only detector refuses **before** any `Database(...)` (were the retire to run, `InvalidArtifactName` would crash the constructor) | no | no |
| FX-C13-MISSING | C13 | `skill_lifecycle_packages(content_artifact_key="custom-skills/s/digest/SKILL.md")` | artifact file absent | missing-file | `missing` — `ArtifactNotFound` swallowed (no-op) if the retire ran; detector still refuses before any construction | no | no |
| FX-C13-SYMLINK | C13 | same row | `artifacts/custom-skills/s/digest/SKILL.md` → in-root symlink | symlink/nonregular | `nonregular` — detector refuses (or, were the retire to run, `unlink()` removes the link only) | no | no |
| FX-C13-DANGLING | C13 | same row | `artifacts/custom-skills/s/digest/SKILL.md` → **dangling** in-root symlink (target absent) | dangling link | `dangling`/`nonregular` — were the retire to run, `path.exists()` is `False` → `ArtifactNotFound` swallowed, the dangling link is **left on disk**; the read-only detector still refuses **before** any construction | no | no |

Every consumer-map row in §2 has at least one fixture; every fixture names a
specific consumer.

### Harness requirements (design only — no test code in this gate)

The registry above describes the shape; the Slice B/C harness must additionally
**prove each control fires**, not merely describe it. Requirements:

- **(i) Unclassified-candidate default refusal (firing proof).** The §2A
  generator self-check is the completeness control: it fails on an unmapped
  emitted column, a duplicate/phantom mapping, or a blank classification. The
  *firing* proof injects a **14th path-bearing column** (or populates a
  `dormant-legacy` path column, e.g. a populated `agent_enrollments.repos`
  value) and asserts the check fails with the named pre-effect default refusal
  (`unclassified_consumer`) **before any capture/import effect** — never a
  silent portable classification.
- **(ii) Populated-dormant default refusal (firing proof).** A **populated**
  `dormant-legacy` column (e.g. a non-null `agent_enrollments.repos` in a legacy
  DB) triggers its named default refusal and **fails the §2A dormant-stays-empty
  assertion**, forcing reclassification — rather than being silently treated
  portable. The dormant-legacy columns carry a recorded live-population proof of
  empty (§2A legacy enumeration); a nonzero population fails the gate. **Active**
  currently-unpopulated columns (`references_manifest` / `assets_manifest`) are
  **excluded** from this dormant control: a non-null active value must not trip
  it.
- **(ii-b) Active-manifest reference default refusal (firing proof).** If an
  **active** manifest/metadata column (`references_manifest` / `assets_manifest`)
  is ever populated with a value that embeds a DB→filesystem reference, the
  export-time validation resolves it against the included, hash-validated roots
  and **default-refuses** any actual unresolved or out-of-policy reference before
  capture/import effects — it is never silently included merely because it sits
  inside an active manifest value.
- **(iii) Observable unsafe-object refusal.** At least one in-root symlink, one
  dangling link, and one nonregular member (dir/device/FIFO), as applicable per
  consumer, produces its named refusal (`nonregular` / `dangling` / `escape`)
  **before** capture/import effects — asserted via the connection-spy (zero
  source connections) and "no archive produced".
- **(iv) Bijective fixture cross-reference.** Every fixture ID in §4 maps 1:1 to
  a §2/§2A consumer row and appears in exactly one harness assertion; the total
  fixture-ID count equals the sum of each consumer's fixture list (no orphan, no
  duplicate, no un-cited ID).

Where a control is required future behavior, the harness must assert it as a
future pre-effect refusal and must **not** infer it from today's resolver
(which may admit/follow in-root symlinks, swallow a dangling link, or leave a
dangling link on disk). The §2A default-refuse matrix is the authoritative
statement of `(now)` vs `(req)`.

---

## 5. Source & destination compatibility detection matrix

Detection order is **fixed and precedes every effect**. The source-side scan is
explicitly ordered (see §6): **filesystem/layout validation and the DB-parent
`-wal`/`-shm` scan come first** — an existing sidecar is a
`source_sidecar_present` refusal with the connection spy at **zero** source
SQLite connections; **only after** that clean scan may a **raw read-only SQLite
connection** inspect `sqlite_master` (for legacy `skill_lifecycle_%` tables,
C13); that read-only inspection is **still strictly before every
`Database(...)` construction** — the constructor itself opens a connection and
can destructively retire legacy artifacts, so it must never be the detection
vehicle. Detection precedes every migration, org-state load, archive
extraction, staging mutation, or conversion. Every non-current case is a
**named refusal** — never auto-upgrade, conversion, or best effort.

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
  (b) assert this count is **zero** for the **sidecar-refusal** paths. **Zero
  does not apply to the clean legacy-inspection path**: after a clean sidecar
  scan, the C13 detector opens exactly **one raw read-only SQLite connection**
  to inspect `sqlite_master` for legacy `skill_lifecycle_%` tables (still
  strictly before any `Database(...)` construction) — that read-only connection
  is permitted and expected; the spy asserts it is **read-only** and
  **pre-constructor**, not that it is absent. Zero-connection is claimed only
  where the refusal precedes any SQLite open (sidecar/layout refusal).
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
