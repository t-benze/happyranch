# Executor Switch Latency & Bounded Rollback — THR-190 PR-A Design

**THR-190 / TASK-5683** | **2026-08-26** | **CODE + TESTS + DOC**

This document normatively specifies the **PR-A** implementation of THR-190:
replacing the whole-workspace executor-switch snapshot/traversal with a
bounded declared-write rollback journal in the executor-switch route. It is
the parity contract for the code shipped in PR-A; later slices (PR-B) must
not silently invalidate any normative statement below.

The THR-190 root cause this design eliminates: `set_agent_executor` took a
full `os.walk` snapshot of the entire agent workspace (every regular file,
including a multi-GB `repos/` tree) solely to compensate a bootstrap
failure — the measured multi-minute `kimi → pi` switch latency.

---

## 1. Scope and boundaries

### 1.1 In scope (PR-A)

- `runtime/daemon/routes/agents.py` — `set_agent_executor` (PUT
  `/agents/{agent_name}/executor`) and its module-private helpers.
- `tests/daemon/test_routes_agents.py` — the executor-switch test suite at
  real seams.
- This latency spec.

### 1.2 Out of scope / PR-B ownership boundary

PR-B owns the adapter/writer layer (`runtime/orchestrator/workspace_adapters.py`,
`SymlinkMaterializer`/link-writer production, their containment-test files,
dependencies). PR-A:

- makes **no production edit** to `workspace_adapters.py` or any adapter
  writer;
- does **not** present `detect_repos=False` (or any equivalent isolation
  knob) as rollback correctness — repo detection is isolation hygiene only;
- requires the **drift-tripwire contract** (§6) to be enforced by tests
  against the real adapter writers; if a future PR-B edit to an adapter
  writer adds a workspace write outside the declared set, the tripwire test
  must fail and the journal declared set must be extended **in the same
  change** that extends the writer — never silently.

Any PR-A change that would require editing a PR-B-owned file is a **serial
integration stop**: do not make the edit; report the serial integration
need instead.

---

## 2. The declared bootstrap write surface

The executor-switch bootstrap writer chain is:

```
set_agent_executor
  -> ContextBuilder.ensure_workspace_ready            (runtime/orchestrator/context_builder.py)
    -> provider workspace adapter ensure_workspace_ready
         (runtime/orchestrator/workspace_adapters.py: Claude / Codex /
          Opencode / Pi)
      -> PersistentWorkspaceSetup.ensure + provider writers
```

Its **entire** write surface is the following declared set
(`_BOOTSTRAP_OWNED_FILES` / `_BOOTSTRAP_OWNED_DIRS` in `routes/agents.py`):

| Path (relative to workspace root) | Writer |
| --- | --- |
| `CLAUDE.md` | claude provider writer |
| `AGENTS.md` | codex / opencode / pi provider writer |
| `.claude/settings.json` | claude provider writer |
| `opencode.json` | opencode provider writer |
| `task_history.md` | `PersistentWorkspaceSetup.ensure` |
| `recent_tasks.md` | legacy rename source (`recent_tasks.md` → `task_history.md`) |
| `memory/_index.md` | `MemoryStore` index (create/regenerate) |
| `.claude` (dir) | claude provider writer (`settings.json` parent) |
| `memory` (dir) | `PersistentWorkspaceSetup.ensure` |

The journal captures **absence/presence/type/content** of exactly this set —
with a distinct **present-but-uncapturable** state for a regular file whose
`read_bytes()` raises `OSError`. Such a file is NEVER represented as absent
(which would let rollback delete it); the preflight (§4 gate 3) rejects it
before any mutation, and — as the authoritative read — the journal's
`capture()` runs in the same Step-0 preflight **before**
`_executor_switch_materialize`, with the route failing closed (400
`executor_bootstrap_failed`) if `capture()` observes any uncapturable
declared file (closing the second-read window where a preflight read
succeeds but a later capture read fails after materialization has already
mutated). The journal's fail-closed backstop still reports a compensation
error rather than deleting an uncapturable file. Canonical skill links
materialized by the union (`.claude/skills/*`, `.agents/skills/*`) and all
other workspace content are **never** touched by capture, bootstrap
compensation, or restore.

---

## 3. No whole-workspace / repos traversal

Normative: neither the successful path, nor journal capture, nor rollback
may **broadly traverse** the workspace or `workspace/repos/`:

- **Forbidden**: `os.walk` anywhere under the workspace; `Path.rglob`
  anywhere under the workspace; `os.scandir` / `Path.iterdir` of the
  workspace root itself (the old snapshot's signature).
- **Permitted (bounded, single-level)**: `iterdir`/`glob` of narrow known
  directories — `workspace/repos/` (repo-name detection),
  `workspace/memory/` (`_index.md` regeneration), and the materializer's own
  skill-root directories.

The route-level guard tests seed sentinel trees (a deep untracked subtree
and a multi-MB file under `repos/`) and fail if any guarded enumeration
occurs or any sentinel is read during successful bootstrap, capture, or
rollback.

---

## 4. Fail-closed preflights (before the first mutation)

Three gates plus the **authoritative rollback capture** run in
`set_agent_executor` **before** `_executor_switch_materialize` (which is
itself a mutation surface over the owned `.claude` directory) and before
any adapter writer, frontmatter, or audit write:

1. **Structured legacy learnings migration gate** — when
   `workspace/learnings/` exists and `workspace/memory/` is absent, bootstrap
   would run the unbounded `learnings/ → memory/` migration
   (`migrate_workspace`), which a bounded journal cannot reverse losslessly.
   The switch fails closed (`400 executor_bootstrap_failed`, named reason
   naming the legacy learnings state) with **zero** mutation: no
   materialization, no bootstrap writer, no frontmatter, no audit.
2. **Unsupported owned-path gate** — any owned path that is a symlink or an
   unsupported non-regular type (directory/FIFO/socket/device) cannot be
   losslessly compensated (write-through mutates an arbitrary external
   target; replace discards the link). The switch fails closed before any
   mutation — critically **before materialization**, so the union
   reconciler can never follow a symlinked owned directory (e.g.
   `workspace/.claude` → external target).
3. **Uncapturable owned-file gate** — a present regular owned file whose
   `read_bytes()` raises `OSError` is materially distinct from an absent
   file: the journal could not capture its content, so a bootstrap failure
   could never restore it (and must never delete it). The switch fails
   closed before any mutation with a named reason identifying the file
   (`read_bytes` failure), never representing it as absent.
4. **Authoritative rollback capture** — `_BootstrapRollbackJournal.capture()`
   runs here, in Step-0 preflight, so every pre-existing declared-write
   target is captured as rollback bytes/state **before the first mutation**.
   The route fails closed (400 `executor_bootstrap_failed`, same named
   reason) if the capture observes any uncapturable declared file — even
   one that gate 3 could read but whose authoritative capture read then
   fails (the TASK-5714 second-read window). Materialization, bootstrap,
   frontmatter, and audit can never run unless capture was lossless.

All four checks run right after read-only request/agent/workspace
resolution, before any mutation. Regular-file switching is unaffected.

---

## 5. Atomic ordering and error contract

The durable order is preserved exactly (journal capture is a read-only
Step-0 preflight step; the supported-input mutation order is unchanged):

```
preflight (fail-closed gates + authoritative journal capture) -> union
materialization -> integrity validation -> bootstrap -> rollback (on
failure) -> frontmatter persistence -> audit
```

- Union/materialization failure → `400 executor_materialization_failed`,
  old executor/frontmatter/audit preserved, no bootstrap write.
- Bootstrap failure after successful union → bounded journal `restore()`:
  pre-existing declared files are restored byte-for-byte, the
  `recent_tasks.md → task_history.md` rename is reversed losslessly, and
  declared artifacts newly created by the failed attempt (`memory/_index.md`,
  empty `memory/`) are removed. Returns `400 executor_bootstrap_failed`;
  old executor/frontmatter/audit preserved. Canonical skill links survive.
- Success → frontmatter + audit, exactly as before.

There is **no conservative full-workspace fallback** anywhere on this path.

---

## 6. Drift-tripwire contract

`tests/daemon/test_routes_agents.py` instruments the **real** adapter
bootstrap call (`ContextBuilder.ensure_workspace_ready`) with filesystem
write/rename spies on the mutation primitives (`Path.write_text`,
`Path.write_bytes`, `Path.rename`, `Path.mkdir`, `Path.unlink`,
`Path.rmdir`, `Path.replace`, `os.replace`, `os.symlink`) and fails if any
write/rename target under the workspace root is outside the declared
`_BOOTSTRAP_OWNED_FILES`/`_BOOTSTRAP_OWNED_DIRS` set. It runs for every
registered provider shape (claude, codex, opencode, pi) with **no adapter
production change**.

Consequence: future adapter evolution that adds a workspace write outside
the declared set fails CI before it can silently outrun the journal.
Extending the adapter writer and the declared set are one atomic change.

---

## 7. Preserved guarantees (regression fence)

- `test_set_executor_materialization_failure_fail_closed`,
  `test_set_executor_materialization_real_missing_source_stops_before_build`,
  and the happy-path tests keep their existing assertions.
- The ordering test proves materialize → validate → bootstrap.
- The symlinked-owned-path tests prove fail-closed before bootstrap, and the
  `.claude`-symlink test proves fail-closed **before materialization** with
  the external sentinel directory untouched.
- `test_set_executor_preflight_rejects_uncapturable_owned_file` proves a
  present regular declared file whose `read_bytes()` raises `OSError` is
  rejected at preflight (400 `executor_bootstrap_failed`) **before**
  materialization/bootstrap/frontmatter/audit, with the original file bytes
  surviving unchanged, and
  `test_bootstrap_journal_uncapturable_present_file_is_not_absent` locks the
  journal's distinct present-but-uncapturable state (never absent; restore
  reports an error instead of deleting the file).
- `test_set_executor_capture_second_read_fails_closed_before_materialize`
  locks the TASK-5714 second-read window: a deterministic per-file call
  counter makes the Step-0 preflight read of a present `CLAUDE.md` succeed
  and the authoritative `_BootstrapRollbackJournal.capture` read fail. The
  real PUT route returns 400 `executor_bootstrap_failed` naming the file
  with the original bytes surviving, and proves union materialization,
  provider bootstrap, frontmatter, and audit all stayed untouched.
- No client contract change: PUT response shape, `before`/`after` fields,
  `stale_files`/`cleaned`/`removed`, and named error codes are unchanged.
