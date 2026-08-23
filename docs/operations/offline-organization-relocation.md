# Offline organization relocation — founder-operated manual runbook (THR-187)

> **Status:** a **manual runbook** for a **one-time, founder-supervised,
> same-slug, offline maintenance-window** move of one existing current-v2 org
> into an **absent** destination slug on another runtime. It is **not** the
> deferred automated archive/import/activation product (Slice B/C), it
> implements no automation, and it makes **no claim** that the shipped runtime
> enforces any inactive/admission state, rebind, or schedule-rearm gate. The
> shipped runtime carries only **Slice A** (read-only preflight + founder-only
> zombie reconciliation). Every command below is an existing, verified surface;
> none is invented.
>
> Evidence for each statement is the shipped code at the current head and the
> Step-0 evidence gate
> `docs/superpowers/specs/org-portability-reference-consumers.md`.

## 1. Scope, assumptions, boundaries, and placeholders

This runbook manually relocates **one existing current-v2 org, under the same
slug**, into an **absent** destination slug, during a founder-maintained
exclusive offline window. In order it: exports the classifier-approved
portable roots → transfers them over a secure channel → privately stages and
validates a candidate on the destination → publishes with a no-overwrite
rename → gates start on a mandatory zero-count quiescence check → starts the
destination and verifies normal discovery.

It is **not** any of: a clone; a source deletion (source deletion is a separate,
later decision — §8); a merge/overwrite of two orgs; a credential or
daemon-token transfer; an automatic rebind/rearm; an online fence or retry
protocol; a v0/v1 layout conversion (every non-current-v2 layout is a named
refusal, never an auto-upgrade).

**Placeholders** (define once; all are absolute, resolved, non-symlink paths):

| Name | Meaning |
| --- | --- |
| `SLUG` | the org slug (identical on source and destination; matches `^[a-z0-9-]{1,40}$`) |
| `SRC_RUNTIME` | source runtime container (the dir containing `happyranch.yaml`) |
| `DST_RUNTIME` | destination runtime container (the dir containing `happyranch.yaml`) |
| `SRC` | `$SRC_RUNTIME/orgs/$SLUG` — source org root |
| `DST` | `$DST_RUNTIME/orgs/$SLUG` — destination org root (**must be absent**, §6) |
| `OP` | a short private operation id, e.g. `2026-08-23-thr187` |
| `STAGE` | private staging dir; on the **destination** machine it **must** be `$DST_RUNTIME/orgs/_pending/$OP` so publication is a same-filesystem rename (§6). On the source machine use `$SRC_RUNTIME/orgs/_pending/$OP` (also reserved) or any other private dir **outside** `$SRC`. |

Hard requirements:

- **Absolute, resolved, non-symlink paths.** Resolve with `cd <dir> && pwd -P`
  (macOS `readlink -f` is not portable). Reject any path that is a symlink or
  contains a symlink component.
- **Founder-private, same-filesystem destination staging** under
  `$DST_RUNTIME/orgs/_pending/$OP`. `_pending` is a reserved slug
  (`runtime/runtime.py::_RESERVED_ORG_SLUGS`) skipped by org enumeration, so it
  is never treated as an org. It is crash residue, **not** a retry protocol.
- **Both daemons stopped** during publication (§6) and before any source SQLite
  open (§3).

## 2. Slice-A preflight / readiness

Slice A ships in the source tree (`runtime/portability/`, the
`/portability-preflight` route) but is **usable against a real source daemon
only after that daemon's deployment has been restarted after its merge**.
Merged ≠ live: do not claim the route is available merely because the code
merged.

1. On the **source** instance only, confirm the running daemon was built from a
   head that includes Slice A, then restart it so the new route is served:
   `scripts/daemon.sh status`, then restart per your normal deploy procedure.
   The **destination** daemon stays stopped for this entire runbook until §7; it
   is never started to serve preflight or validate a staged payload.
2. Run the preflight (read-only — creates no archive, staging, fence, or
   cancellation, and reports possible zombies but does **not** resolve them):

   ```bash
   happyranch orgs portability-preflight "$SLUG"
   ```

   Use it **only** when the route is actually live (step 1). It prints root
   classification, eligibility blockers, possible zombies, and remedies.
3. Resolve every printed blocker using **only** the printed remedies — they
   reference existing founder controls. There is **no** relocation-specific
   disarm/cancel command; never invent one. The real controls are:

   - armed schedules → `happyranch todos pause --org "$SLUG" <schedule-id>`
     (or `happyranch todos cancel --org "$SLUG" <schedule-id>`);
   - a **firing** schedule → no pause/cancel exists; wait for it to reach a
     terminal state, then re-run preflight;
   - nonterminal tasks → `happyranch cancel <task-id> --org "$SLUG"`;
   - active jobs → `happyranch jobs stop <job-id> --org "$SLUG"`;
   - live sessions / queued tasks / pending thread invocations / active dreams /
     active work-hours → no founder cancel control exists; wait for them to
     complete, then re-run preflight;
   - a **possible zombie** (reported only) → resolve via the **founder-only,
     confirmed-zombie** scope only:
     `happyranch orgs reconcile-portability "$SLUG" --from-file <absolute-json-path>`
     with JSON `{"candidate_task_id": "…", "disposition": "cancel", "evidence":
     {…}}` (`disposition` may also be `consume_result`). This route revalidates
     exactly one candidate as a true zombie and refuses otherwise; it is **not**
     a general disarm control. A parked task (`block_kind` set) is never a
     zombie.

   Re-run preflight until it prints `portability: eligible` with no rejections
   and no blockers.

## 3. Stop both daemons, then export the source

**Stop both daemons.** On the source (it was running to serve preflight) and
confirm the destination is still stopped:

```bash
scripts/daemon.sh stop --force     # default daemon home requires --force
scripts/daemon.sh status           # must print "not running" (exit non-zero)
```

`stop --force` is the documented default-home stop; the `--force` guard exists
only for the **default** home (`HAPPYRANCH_DAEMON_HOME` unset), while an
isolated instance (`HAPPYRANCH_DAEMON_HOME` set) skips the guard. **Never**
remove daemon lifecycle/auth files (`daemon.token`, `daemon.pid`,
`daemon.port`, `daemon.log`, `runtimes.yaml`, `runtime-audit.db`,
`config.yaml`); the stop script removes only its own pid/port files.

**1. Sidecar gate — before any SQLite open.** Check for pre-existing WAL/SHM
residue at the resolved DB paths:

```bash
test ! -e "$SRC/happyranch.db-wal" && test ! -e "$SRC/happyranch.db-shm" \
  && echo "no sidecars" || echo "SIDECAR PRESENT — STOP"
```

If **either** sidecar exists, **stop and refuse**: do not open SQLite, do not
delete anything. Their presence is independent proof of active or unclean
access (even when `status` reads "not running" — a live-but-idle daemon passes a
lock test but still leaves sidecars). Diagnose/resolve through the documented
daemon procedure, then restart this runbook from §2. Never delete a
pre-existing sidecar yourself.

**Why raw `.db` copying is unsafe.** In WAL mode, committed transactions live in
`happyranch.db-wal` and are folded into the main file only at a checkpoint. A
raw copy of `happyranch.db` alone can therefore miss committed-but-not-yet-
checkpointed rows (Step-0 harness fixture (d) proves a raw `.db` copy reads
0 rows where a logical snapshot reads the committed rows). The logical snapshot
below reads through WAL without mutating or checkpointing the source.

**2. Logical snapshot (no source mutation).** Use the `sqlite3` backup API
through a read-only open. First verify your `sqlite3` build supports it on a
throwaway database:

```bash
tmp=$(mktemp -d) && sqlite3 "$tmp/t.db" "CREATE TABLE t(x);" && \
  sqlite3 -readonly "$tmp/t.db" ".backup '$tmp/s.db'" && \
  sqlite3 "$tmp/s.db" "PRAGMA integrity_check;" && rm -rf "$tmp"   # expect "ok"
```

Then take the real snapshot:

```bash
sqlite3 -readonly "$SRC/happyranch.db" ".backup '$STAGE/happyranch.db'"
```

(There is no fictional `happyranch export` command; `.backup ?DB? FILE` with
default DB `main` is the real primitive.)

**3. Validate the staged snapshot:**

```bash
sqlite3 "$STAGE/happyranch.db" "PRAGMA integrity_check;"    # expect "ok"
sqlite3 "$STAGE/happyranch.db" "PRAGMA foreign_key_check;"  # expect no rows
```

## 4. Build the allow-listed manifest and archive (portable roots only)

Carry **only** what the shipped classifier `runtime/portability/roots.py::
classify_root_entries` approves. It is **not** the bare `ALLOWED_ROOTS` set:
`skills/` and `workspaces/` are special-cased there, so iterating
`ALLOWED_ROOTS` alone would silently drop agent memory and valid legacy skills.

Portable roots: the logical `happyranch.db` snapshot (not a raw copy); `org/`
(whole tree); `artifacts/`, `kb/`, `threads/`, `task-attachments/`, `jobs/`,
`dreams/`, `work_hours/`, `schedules/`, `talks/`; `skills/` only where each
package passes the classifier's legacy-skill validation; and
`workspaces/<agent>/memory/**` **only**.

Never carried: `happyranch.db-wal`/`-shm`; generated markers
(`.hr_review_renamed`, `.org_settings_seeded`); `dashboard_projection.json`;
caches (`.pytest_cache`, `.DS_Store`); legacy residue DBs (`audit.db`,
`db.sqlite3`) unless zero-byte-and-excluded by the classifier; every
`workspaces/*` subtree except `memory` (including `output`, `repos`,
bootstrap/settings); and any unknown or nonregular entry. **Any direct child
the classifier does not explicitly allow or explicitly exclude is a rejection
— stop, do not guess.**

Copy the allow-listed roots into `$STAGE/org-payload/` (place the snapshot as
`org-payload/happyranch.db`). For the memory-only carve-out, use a
`find`-based selection rather than rsync filters — macOS ships `openrsync`,
whose `--include`/`--exclude` semantics differ from GNU rsync, so a portable
`find` + `cp -R` loop is safer:

```bash
mkdir -p "$STAGE/org-payload"
# … copy each whole-tree portable root (org, kb, talks, …) with cp -R …
cp "$STAGE/happyranch.db" "$STAGE/org-payload/happyranch.db"
while IFS= read -r memdir; do
  rel="${memdir#"$SRC"/}"                 # e.g. workspaces/alice/memory
  mkdir -p "$STAGE/org-payload/$(dirname "$rel")"
  cp -R "$memdir" "$STAGE/org-payload/$rel"
done < <(find "$SRC/workspaces" -mindepth 2 -maxdepth 2 -type d -name memory -print)
```

Prove the carve-out with a disposable dry-run fixture before relying on it:
populate a scratch `workspaces/<agent>/` with a `memory/` dir plus `repos/`,
`output/`, and a token file, run the loop, and confirm `find
"$STAGE/org-payload" -type f` lists **only** files under `…/memory/` and no
token/output/repo bytes.

Then reject any nonregular member at any depth (symlink, device, FIFO, socket):

```bash
find "$STAGE/org-payload" \( -type l -o -type p -o -type s -o -type b -o -type c \) -print
# non-empty output ⇒ STOP: remove the offending member from the SOURCE org and
# re-run preflight; never "fix" it in the payload
```

**Record a verifiable manifest + checksums**, then archive and hash it:

```bash
(cd "$STAGE/org-payload" && find . -type f -print0 | sort -z | \
  xargs -0 shasum -a 256) > "$STAGE/manifest.txt"        # macOS
# Linux: replace shasum -a 256 with sha256sum
tar -czf "$STAGE/org-archive.tar.gz" -C "$STAGE/org-payload" .
shasum -a 256 "$STAGE/org-archive.tar.gz"                 # record this value
```

**The source is preserved fully** — nothing here mutates or deletes it.

## 5. Transfer (plaintext) and destination-side validation

Transfer `org-archive.tar.gz` and `manifest.txt` over a founder-selected
**secure** channel. The archive is unsigned, unencrypted local plaintext; its
hash is integrity evidence, not authentication or confidentiality. On arrival,
verify the archive hash matches the recorded value, then re-inspect
`manifest.txt`.

On the **destination** machine (`STAGE=$DST_RUNTIME/orgs/_pending/$OP`, same
filesystem as `orgs/`):

1. **Destination stopped, slug absent** (including any symlink/broken entry):

   ```bash
   scripts/daemon.sh status                          # "not running"
   test ! -e "$DST" && test ! -L "$DST" && echo "slug absent"
   ```

2. **Validate every member path before/after extract**: reject any absolute
   path, `..` traversal, duplicate path, path escaping the staging root,
   symlink/dangling link, device/FIFO/nonregular member, or any member not in
   the §4 allow-list. Verify the allow-list of every member against §4 and the
   recorded manifest.

3. **Validate the staged DB and references**: run
   `PRAGMA integrity_check` (expect `ok`) and `PRAGMA foreign_key_check` (expect
   no rows) against the staged `happyranch.db`. As the manual form of the
   deferred reference validation, confirm each DB-held filesystem reference in
   the Step-0 consumer map (C1–C13) resolves to a staged regular file with no
   symlink/escape. Treat any missing, escaping, symlinked, or data-shaped
   refusal — populated `custom_skill_versions.references_manifest` /
   `assets_manifest` (C12b/C12c) or a populated `skill_lifecycle_packages`
   legacy table (C13) — as a stop. If you cannot confirm a consumer resolves,
   escalate; do not guess.

4. Confirm the staged candidate is private, complete, and startable-shaped: it
   must contain a valid `org/teams.yaml` and a `happyranch.db`, and be readable
   only by the founder. It remains a **candidate**, not an org, until §6.

## 6. Manual publication (founder-exclusive, no-overwrite)

Publication is a **manual, exclusive-access, check-and-publish** operation, not
a shipped atomic importer. It requires an exclusive founder-owned window with
**both daemons stopped and no other actor writing `$DST_RUNTIME/orgs/`** (no
agent sessions, jobs, or other admin — including any process on any machine
sharing that runtime).

1. **Re-check the slug is absent immediately before publish** (repeat §5 step 1).
2. **Publish with an atomic same-filesystem rename.** Because the target is
   verified absent and the window is exclusive, `mv` performs an atomic
   `rename(2)` with nothing to overwrite or merge:

   ```bash
   mv "$STAGE" "$DST"
   ```

   **Do not use `mv -n`**: on BSD/macOS `mv -n` still moves a source directory
   *into* an existing destination directory (a directory-then-contents merge)
   rather than refusing, and GNU `mv -n` likewise only skips overwriting
   existing *files*. It is **not** a portable no-clobber guarantee for directory
   publish. Do not substitute `cp -r`, `os.replace`, or a plain
   `mv`/`mkdir`+move-contents merge. If you cannot guarantee an exclusive
   window and an absent target, **STOP** and escalate (§8) rather than risk a
   partially populated, discoverable org.
3. **Verify publication postconditions** (both must hold):

   ```bash
   test -d "$DST" && test -f "$DST/org/teams.yaml" && echo "target exists"
   test ! -e "$STAGE" && echo "stage consumed"
   ```

   `$DST` must exist with `org/teams.yaml` and `happyranch.db`; `$STAGE` must be
   gone (the rename consumed it).

## 7. Mandatory pre-start zero-count gate, then start and verify

**Before any destination start**, run two **separate, read-only** queries
against the **published** `$DST/happyranch.db`. The destination may start only
if **both** print `|0`:

```bash
sqlite3 -readonly "$DST/happyranch.db" \
  "SELECT 'schedules_armed_or_firing', COUNT(*) FROM schedules WHERE status IN ('armed','firing');"
sqlite3 -readonly "$DST/happyranch.db" \
  "SELECT 'tasks_pending_in_progress_escalated', COUNT(*) FROM tasks WHERE status IN ('pending','in_progress','escalated');"
```

If either count is nonzero, **stop**: do not patch the DB, do not overwrite, do
not start. The export was not taken from a quiescent source — preserve
everything (§8) and redo the export after resolving quiescence at the source
(§2). The status strings come from the current runtime code
(`ScheduleStatus.armed`/`firing`, `TaskStatus.pending`/`in_progress`/
`escalated` in `runtime/models.py`); the tables are `schedules` and `tasks`
with a `status TEXT` column (`runtime/infrastructure/database.py`), and the
per-org DB file is `happyranch.db` (`runtime/orchestrator/_paths.py`).

This query is a **manual safety precondition** — it is not a shipped feature,
not a disarm command, and it performs no mutation.

**Then start the destination** using the documented daemon script and verify
normal discovery/health:

```bash
scripts/daemon.sh start
scripts/daemon.sh status     # expect "running (pid …, port …)"
happyranch orgs              # the relocated slug must appear with its root
happyranch web               # verifies GET /api/v1/health is reachable
```

**How the restored org loads (the actual truth).** There is **no imported-
inactive marker, no inactive/admission feature, and no rebind gate** in the
shipped runtime. Once the daemon starts, org discovery is the normal existing
behavior: `RuntimeDir.iter_org_roots` (`runtime/runtime.py`) yields every valid
org directory whose name matches the slug pattern and which contains
`org/teams.yaml`, skipping only the reserved `_pending`/`_archive` names. A
restored root containing `org/teams.yaml` is therefore discovered and loaded as
a **normal active org**. First load may perform ordinary existing DB migrations
or settings seeding — there is no data-safe inactive state.

**Rebinding target-local executors/adapters** and **later re-arming schedules**
are ordinary operator work *after* start; the runtime does **not** enforce
either, and neither blocks startup. This manual start is allowed only because
the §7 zero-count gate passed and the source was quiescent when exported.

## 8. Failure handling and escalation

- **Before publication (staging/validation):** retain the source intact and
  clean **only** the exact `_pending/$OP` operation directory you created, after
  inspection, with both daemons stopped. Never `rm -rf` broadly, never touch
  `_pending` beyond your own operation directory. Re-run from §2 after fixing
  the cause.
- **After publication but before start** (a nonzero §7 count or a failed §6
  postcondition): do **not** overwrite, re-run, or merge the published tree, and
  do **not** hand-edit the DB. Leave the destination **stopped**, preserve the
  source intact, record the evidence (the §7 counts, `scripts/daemon.sh status`,
  the §6 postcondition output), and escalate.
- **Post-start:** verify discovery/health via the existing documented surfaces.
  If the relocated org does not appear or the health check fails, leave the
  destination stopped and escalate with the recorded evidence.

This runbook makes **no automatic rollback claim** and no runtime-admission or
fencing claim; it is a manual procedure and every failure path stops, preserves
source, and escalates.

**Source deletion** is a separate, later decision, considered only after the
relocated org is published, started, and verified on the destination. This
runbook's success criteria do not include it.

## Source-of-truth references

- Classifier: `runtime/portability/roots.py` (`classify_root_entries`).
- Eligibility: `runtime/portability/eligibility.py` (`compute_eligibility`).
- Preflight/reconcile routes: `runtime/daemon/routes/portability.py`
  (`GET /portability-preflight`, `POST /reconcile-portability`).
- Daemon lifecycle: `scripts/daemon.sh` (`start` / `stop [--force]` / `status`).
- Runtime layout + reserved slugs: `runtime/runtime.py`
  (`RuntimeDir.iter_org_roots`, `_RESERVED_ORG_SLUGS`).
- Per-org DB filename: `runtime/orchestrator/_paths.py` (`db_path` →
  `happyranch.db`).
- Status enums + tables: `runtime/models.py` (`TaskStatus`, `ScheduleStatus`);
  `runtime/infrastructure/database.py` (`tasks`, `schedules`).
- DB-to-filesystem reference map:
  `docs/superpowers/specs/org-portability-reference-consumers.md`.
