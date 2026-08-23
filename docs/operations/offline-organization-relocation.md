# Offline organization relocation — founder-operated manual restore runbook (THR-187)

> **Status:** a manual runbook for a **one-time, founder-operated, offline
> maintenance-window** move. It covers the complete manual path — **export,
> transfer, validation, private destination staging, publication, and
> destination start** — performed with ordinary `sqlite3`, `tar`, and checksum
> tooling inside a founder-owned exclusive maintenance window. It is **not**
> the deferred automated archive/import/activation product (Slice B/C), it
> implements no automation, and it makes no claim that the shipped runtime
> enforces any inactive/admission marker. The current `runtime/` head ships only
> **Slice A** (read-only preflight + founder-only zombie reconciliation,
> PR #680). Everything else below is a manual procedure.
>
> Evidence for every statement here is the shipped code at the current head and
> the Step-0 evidence gate at
> `docs/superpowers/specs/org-portability-reference-consumers.md`.

## 1. Product boundary — what this procedure is and is not

This runbook manually relocates **one existing current-v2 org, under the same
slug**, into an **absent** destination slug on another (or the same) runtime,
during a founder-maintained offline window. In order it:

1. **exports** the org's classifier-approved portable roots,
2. **transfers** them over a secure channel,
3. **privately stages and validates** a candidate payload on the destination,
4. **publishes** that staged payload into the live destination tree with a
   no-overwrite rename,
5. **gates** the destination start on a mandatory zero-count quiescence check
   (see §13), and
6. **starts** the destination daemon and verifies normal org discovery.

It is **not** any of the following, and you must not attempt any of them as part
of this move:

- the **deferred automated archive/import/activation product** (Slice B/C) —
  nothing here ships, automates, or assumes that product;
- a clone, a source deletion, or a merge/overwrite of two orgs;
- credential or daemon-token transfer, or an automatic rebind/rearm;
- an online fence, a retry/receipt protocol, or runtime admission/enforcement;
- a v0/v1 layout conversion (every non-current-v2 layout is a named refusal,
  never an auto-upgrade).

**Source deletion remains a separate, later decision** — see §15. This runbook
never deletes or mutates the source org, and nothing in it authorizes source
deletion.

## 2. How the destination loads the restored org (runtime discovery truth)

There is **no inactive/admission marker** and **no shipped Slice-B
inactive-admission implementation**. This runbook does **not** create, require,
or rely on any `org/.happyranch-imported-inactive.json` file — no such file
exists in the runtime, and none is written here.

Once the destination daemon starts, org discovery is the **normal existing
behavior**: `RuntimeDir.iter_org_roots` (`runtime/runtime.py`) yields every
valid org subdirectory under `<dest-runtime>/orgs/` whose name matches the slug
pattern and which contains `org/teams.yaml`. Only the two reserved names
`_pending` and `_archive` are skipped (`_RESERVED_ORG_SLUGS`). A restored root
that contains `org/teams.yaml` is therefore discovered and loaded as a **normal
active org** — there is no "imported inactive" or "non-operational" state in the
shipped runtime, and no admission classifier to refuse it.

This is why the manual start in §14 is gated **exclusively** on the mandatory
zero-count quiescence check (§13) plus the exclusive maintenance window — never
on any claim of a shipped marker or admission enforcement.

## 3. Security and data-safety warnings (read before touching anything)

- **Archives are unsigned, unencrypted local plaintext.** The `.tar.gz` produced
  by this procedure carries your org's DB and KB/talks/memory in plaintext. You
  are responsible for choosing a **secure transfer channel** (e.g. an encrypted
  copy over a trusted transport). The archive file hash in §10 is integrity
  evidence, not authentication or confidentiality.
- **Never copy a whole org folder or a whole workspace tree.** Workspace trees
  (`workspaces/<agent>/...`) can contain **test-harness daemon tokens** and other
  credentials materialized during bootstrap. Copying them wholesale can exfiltrate
  tokens. Only the classifier-approved roots in §5 are carried, and even there the
  workspace exception is the single `memory` subtree.
- **Never transfer daemon lifecycle/auth files, credentials, repos, or
  worktrees.** This includes `~/.happyranch/` (or `$HAPPYRANCH_DAEMON_HOME`)
  files such as `daemon.token`, `daemon.pid`, `daemon.port`, `daemon.log`,
  `runtime-audit.db`, `runtimes.yaml`, and `config.yaml`; any executor/CLI
  credential; and `workspaces/*/repos` / ordinary bootstrap / output trees.
- **Never carry SQLite `-wal`/`-shm` sidecars** (§9). A raw copy of `happyranch.db`
  alone is **unsafe**: committed WAL frames can be absent from the main DB file
  (the Step-0 source-sidecar harness design, fixture (d), proves a raw `.db`
  copy reads **0 rows** where a logical snapshot reads the committed rows). Use
  the logical snapshot in §9.

## 4. Prerequisites

On **both** the source and destination machines you need:

- the `sqlite3` CLI (version with `.backup` support — any modern build);
- `tar` and a SHA-256 checksum tool (`shasum -a 256` on macOS, `sha256sum` on
  Linux);
- a private staging directory **outside any org's DB-parent directory**
  (a DB parent is `<runtime>/orgs/<slug>/`; staging must not sit inside it);
- **absolute, non-symlink** paths for source org, destination runtime, and
  staging. Resolve everything with `realpath` (macOS: `readlink -f` is not
  portable — use `cd <dir> && pwd -P`). Reject any path that is a symlink or
  contains a symlink component.

The source org root is `<source-runtime>/orgs/<source-slug>`; the destination
publish target is `<dest-runtime>/orgs/<source-slug>` (same slug). The
destination slug must be **absent** (§12).

## 5. Portable roots — the allow-list (derived from the shipped classifier)

The shipped classifier `runtime/portability/roots.py`
(`classify_root_entries`) is the single source of truth for what is portable.
It is **not** the bare `ALLOWED_ROOTS` constant: `skills/` and `workspaces/`
are special-cased there, so iterating `ALLOWED_ROOTS` would silently drop agent
memory and valid legacy skills.

Carry **only** these, each verified regular (no symlink, no special file) at
every depth:

| Root | Note |
| --- | --- |
| `happyranch.db` | **not** copied raw — replaced by the logical snapshot (§9) |
| `org/` | whole tree |
| `kb/` | per-org knowledge base |
| `talks/` | talk transcripts |
| `threads/` | thread transcripts + scoped attachments |
| `task-attachments/` | task attachment blobs |
| `jobs/` | job logs/scripts |
| `dreams/` | dream transcripts |
| `work_hours/` | work-hours transcripts |
| `schedules/` | schedule transcripts |
| `artifacts/` | org-shared blob store (artifacts + custom-skill content) |
| `skills/` | **valid** legacy skill packages only (see below) |
| `workspaces/*/memory/**` | the **sole** workspace carve-out |

Explicitly **never** carried: `happyranch.db-wal` / `happyranch.db-shm`,
generated markers (`.hr_review_renamed`, `.org_settings_seeded`),
`dashboard_projection.json`, caches (`.pytest_cache`, `.DS_Store`), legacy
residue DBs (`audit.db`, `db.sqlite3`), `workspaces/*/output`, and every other
`workspaces/*` subtree except `memory`. Any direct child the classifier does not
explicitly allow or explicitly exclude is a **rejection** — stop, do not guess.

`skills/` is carried only where each package passes the classifier's legacy-skill
validation (`skill.yaml` slug/id/source conformance, nonempty `SKILL.md` starting
with `#`, members limited to `SKILL.md`/`skill.yaml`/`references`/`assets` with
regular files only). A `system_contract` skill and any symlink/nonregular member
are rejections.

The Step-0 reference-consumer map
(`docs/superpowers/specs/org-portability-reference-consumers.md` §2) is the
authoritative cross-check for which DB-held values resolve into these roots
(C1–C13). Two data-shaped refusals are binding even in the manual procedure:
populated `custom_skill_versions.references_manifest` / `assets_manifest`
(C12b/C12c) and any populated `skill_lifecycle_packages` legacy table (C13) are
**named refusals** — do not carry or attempt to interpret them.

## 6. Phase A — Deploy and calibrate Slice-A preflight on the source only

Slice A (PR #680) must be **deployed and running** on the **source** instance —
not merely merged. The **destination daemon must remain stopped throughout this
entire runbook** until §14; it is never started or restarted for preflight,
calibration, validation, or any other reason.

1. On the **source** instance only, confirm the running daemon is built from a
   head that includes Slice A, then restart it so the new route is live:
   `scripts/daemon.sh status` (calibrate the actual stop/start against the real
   daemon), then restart per your normal deploy procedure. **Merge ≠ live
   deployment** — do not proceed until the source daemon actually serves the
   `/portability-preflight` route.
2. Do **not** start, restart, or otherwise activate the **destination** daemon
   at any point before §14. It stays stopped for the entire procedure and is
   never used to serve preflight or to validate a staged payload.
3. Calibrate on the **source** instance:

   ```bash
   happyranch orgs portability-preflight <source-slug>
   ```

   This is read-only (creates no archive, staging, fence, or cancellation) and
   prints root classification, eligibility blockers, possible zombies (reported,
   **not** resolved), and remedies.

## 7. Phase B — Resolve every source preflight blocker

Re-run `happyranch orgs portability-preflight <source-slug>` until it prints
`portability: eligible` with **no rejections and no blockers**. Use only the
remedies the command prints — they reference **existing** founder controls;
there is no relocation-specific disarm/cancel command. The real controls are:

- nonterminal tasks → `happyranch cancel <task-id> --org <source-slug>`;
- active jobs → `happyranch jobs stop <job-id> --org <source-slug>`;
- armed schedules → `happyranch todos pause --org <source-slug> <schedule-id>`
  (or `happyranch todos cancel --org <source-slug> <schedule-id>`);
- a **firing** schedule → no pause/cancel exists; wait for it to reach a
  terminal state, then re-run preflight;
- live sessions / queued tasks / pending thread invocations / active dreams /
  active work-hours → no founder cancel control exists; wait for them to
  complete, then re-run preflight;
- a **possible zombie** (reported only) → resolve it via the founder-only,
  confirmed-zombie scope only:
  `happyranch orgs reconcile-portability <source-slug> --from-file <absolute-json-path>`
  where the JSON is `{"candidate_task_id": "…", "disposition": "cancel",
  "evidence": {…}}` (`disposition` may also be `consume_result`). This route
  revalidates **exactly one** candidate as a true zombie and refuses otherwise;
  it is **not** a general disarm/cancel control, so do not use it for anything
  but a confirmed zombie. A parked task (`block_kind` set) is never a zombie.

Resolve classifier **rejections** (unknown roots, nonregular members, nonzero
legacy-residue DBs, invalid skill packages) by hand-editing the org filesystem
to the allow-list in §5 — never by guessing a new portable root.

**Quiescence is resolved at the source, before export, through these existing
lifecycle/Todo controls.** Never tell an operator to patch the SQLite DB or
invent a new disarm command to reach quiescence.

## 8. Phase C — Stop the source daemon; destination stays stopped

The **source** daemon was running to serve preflight (§6); stop it now. The
**destination** daemon was never started and must remain stopped — do not start
it here or anywhere else before §14.

On the **source** instance:

```bash
scripts/daemon.sh stop --force
scripts/daemon.sh status    # must exit non-zero / print "not running"
```

On the **destination** instance, verify it is stopped (it was never started in
this runbook):

```bash
scripts/daemon.sh status    # must exit non-zero / print "not running"
```

`scripts/daemon.sh stop --force` is the documented default-home stop. The
`--force` guard exists only for the **default** daemon home
(`$HAPPYRANCH_DAEMON_HOME` unset); an isolated instance
(`HAPPYRANCH_DAEMON_HOME` set) skips the guard — retain that context when you
have an alternate daemon home. **Never** remove daemon lifecycle/auth files
(`daemon.token`, `daemon.pid`, `daemon.port`, `daemon.log`,
`runtime-audit.db`) by hand; the stop script removes only its own pid/port
files.

Verify `status` reports stopped on the source (and still stopped on the
destination) before the next phase.

## 9. Phase D — Export (sidecar gate → logical snapshot → payload → archive)

Run this on the source machine. `$STAGE` is your private staging dir (absolute,
non-link, **outside** the source org DB parent). `$ORG=<source-runtime>/orgs/<source-slug>`.

**1. Sidecar gate — before any SQLite open.** List the resolved DB parent and
check for `happyranch.db-wal` and `happyranch.db-shm`:

```bash
ls -la "$ORG" | grep -E 'happyranch\.db-(wal|shm)' || echo "no sidecars"
```

If **either** sidecar exists, **stop and refuse**: do not open SQLite, do not
delete anything. Their presence is independent proof of active or unclean
access (even when pid/port evidence reads "stopped" — a live-but-idle daemon
passes a lock test but still leaves sidecars). Resolve under the documented
daemon procedure: identify and stop the holder, or resolve stale residue. Never
delete a pre-existing sidecar yourself.

**2. Logical snapshot (no source mutation).** Use the `sqlite3` backup API
through a read-only open — a logical snapshot that includes committed WAL
frames and never checkpoints or mutates the source:

```bash
sqlite3 -readonly "$ORG/happyranch.db" ".backup $STAGE/happyranch.db"
```

(No fictional `happyranch export` command exists; this is the real primitive —
`.backup ?DB? FILE` with default DB `main`.)

**3. Validate the staged snapshot:**

```bash
sqlite3 "$STAGE/happyranch.db" "PRAGMA integrity_check;"   # expect "ok"
sqlite3 "$STAGE/happyranch.db" "PRAGMA foreign_key_check;" # expect no rows
```

**4. Build the allow-listed payload.** Copy **only** the §5 roots into
`$STAGE/org-payload/`, including the staged snapshot as `happyranch.db`. Exclude
every sidecar, generated marker, cache, and non-allow-listed workspace subtree.
Then reject any member that is a symlink, dangling link, or nonregular
file/dir/device/FIFO at any depth:

```bash
find "$STAGE/org-payload" \( -type l -o -type p -o -type s -o -type b -o -type c \) -print
# non-empty output ⇒ stop: remove the offending member from the source org
# (per §5) and re-run preflight; never "fix" it in the payload
```

**5. Record a manifest + checksums.** For each staged member, record
`path`, `type`, `bytes`, and `SHA-256`; then compute the archive SHA-256:

```bash
(cd "$STAGE/org-payload" && find . -type f -print0 | sort -z | \
  xargs -0 shasum -a 256) > "$STAGE/manifest.txt"          # macOS
# Linux: … xargs -0 sha256sum …
```

**6. Archive:**

```bash
tar -czf "$STAGE/org-archive.tar.gz" -C "$STAGE/org-payload" .
shasum -a 256 "$STAGE/org-archive.tar.gz"                  # record this value
```

**Source is preserved fully.** Source deletion is a **separate** decision made
only after a published, started, and validated destination exists (§15); do
nothing to the source org here. Leave the source daemon stopped for the
remainder of this runbook — no step restarts it.

## 10. Phase E — Transfer

Copy `org-archive.tar.gz` and `manifest.txt` to the destination machine over
your chosen **secure** channel. The archive is unsigned, unencrypted plaintext
(§3). Verify the archive SHA-256 on arrival matches the value you recorded:

```bash
shasum -a 256 org-archive.tar.gz   # must equal the recorded value
```

## 11. Phase F — Destination staging and validation (private `_pending`)

Run this on the destination machine. `$DST=<dest-runtime>/orgs/<source-slug>`;
staging is `<dest-runtime>/orgs/_pending/<operation-id>` (the `_pending` name is
reserved and skipped by the runtime's org enumeration, so it is never treated as
an org). `_pending` is crash residue only — **not** a retry protocol. The staged
candidate must live on the **same filesystem** as `<dest-runtime>/orgs/` so the
§12 publication is a rename, not a copy.

**1. Verify archive integrity before extraction.** Re-confirm the archive
SHA-256 matches the recorded value. Inspect `manifest.txt`.

**2. Confirm the destination is stopped and the slug is absent:**

```bash
scripts/daemon.sh status                        # "not running"
test ! -e "$DST" && test ! -L "$DST" && echo "slug absent"
```

The slug must be absent including any symlink or broken entry.

**3. Extract into private staging and reject unsafe members.** Extract into
`<dest-runtime>/orgs/_pending/<operation-id>/`, then reject before doing
anything else:

- any duplicate path, absolute path, `..` traversal, or path escaping the
  staging root;
- any symlink, dangling link, or nonregular member (dir/device/FIFO) at any
  depth;
- any member not in the §5 allow-list.

Verify the allow-list of **every** member against §5 and the recorded manifest.

**4. Validate the staged DB and reference map.** Run
`PRAGMA integrity_check` (expect `ok`) and `PRAGMA foreign_key_check` (expect no
rows) against the staged `happyranch.db`. Then, as the manual form of the
deferred reference validation, confirm each DB-held filesystem reference in the
Step-0 map (C1–C13) resolves to a staged regular file with no symlink/escape;
treat any missing, escaping, symlinked, or data-shaped refusal (C12b/C12c/C13)
as a stop. If you cannot confirm a consumer resolves, escalate — do not guess.

**5. Confirm the staged candidate is private, complete, and startable-shaped.**
The staged tree must contain a valid `org/teams.yaml` and a `happyranch.db`, and
must be readable only by the founder (private). It is a **candidate for
publication**, not yet an org — it stays under `_pending/<operation-id>` until
§12. The destination daemon remains stopped.

## 12. Phase G — Manual publication (founder-exclusive, no-overwrite)

This is a **manual, exclusive-access, check-and-publish** operation. It is not a
claim of a shipped atomic importer, and there is no automated publication
capability in the runtime. Publication requires an **exclusive founder-owned
maintenance window with both daemons stopped and no other actor allowed to write
`<dest-runtime>/orgs/`** (no agent sessions, no jobs, no other admin — including
any process on any machine sharing that runtime).

**1. Re-verify the destination is stopped and the slug is still absent**
(repeat §11 step 2). Because publication is a same-filesystem rename, the
candidate was staged under `<dest-runtime>/orgs/_pending/<operation-id>` (§11).

**2. Publish with a no-overwrite rename.** After verifying `mv`'s no-clobber
option on your platform — macOS `mv` and GNU `mv` both accept `-n` ("do not
overwrite an existing file") — publish the whole operation directory to the
previously absent slug:

```bash
mv -n "$STAGE_DIR" "$DST"
```

where `$STAGE_DIR=<dest-runtime>/orgs/_pending/<operation-id>` and
`$DST=<dest-runtime>/orgs/<source-slug>`.

**3. Verify the postconditions.** The publish succeeded only if **both** hold:

```bash
test -d "$DST" && test -f "$DST/org/teams.yaml" && echo "target exists"
test ! -e "$STAGE_DIR" && echo "stage is gone"
```

`$DST` must now exist and contain the staged `org/teams.yaml` and
`happyranch.db`, and `$STAGE_DIR` must be gone (the rename consumed it). The
`-n` flag refuses on collision: if `$DST` exists at the moment of the rename,
the command does **not** overwrite or merge it. If your platform's `mv` cannot
guarantee no-clobber semantics, **STOP** — do not substitute `cp -r`,
`os.replace`, or a plain `mv`/`cp`, and do not `mkdir "$DST"` then move contents
in. Escalate (§15) rather than risk a partially populated, discoverable org.

This runbook does **not** silently overwrite or merge existing roots, and it
does **not** create or rely on any inactive marker.

## 13. Phase H — Pre-start quiescence gate (mandatory, both counts zero)

After publication and **before** any destination start, run this manual safety
precondition against the published DB. It uses the **actual published path** and
a **read-only** `sqlite3` invocation, and reports **two separate counts**. The
destination may start only if **both** are zero:

```bash
sqlite3 -readonly "$DST/happyranch.db" \
  "SELECT 'schedules_armed_or_firing', COUNT(*) FROM schedules WHERE status IN ('armed','firing');"
sqlite3 -readonly "$DST/happyranch.db" \
  "SELECT 'tasks_pending_in_progress_escalated', COUNT(*) FROM tasks WHERE status IN ('pending','in_progress','escalated');"
```

Both must print `|0`. **Do not start if either count is nonzero.**

- The status strings come from the **current runtime code**:
  `ScheduleStatus` (`runtime/models.py`) includes `armed` and `firing`;
  `TaskStatus` (`runtime/models.py`) includes `pending`, `in_progress`, and
  `escalated`. The tables are `schedules` and `tasks` with a `status` TEXT
  column (`runtime/infrastructure/database.py`), and the per-org DB file is
  `happyranch.db` (`runtime/orchestrator/_paths.py`).
- This query is a **manual safety precondition only** — it is not a shipped
  feature, not a disarm command, and not a mutation. It is read-only.
- **All schedule/task quiescence must have been resolved at the source, before
  export, through the existing lifecycle/Todo controls in §7.** Never tell an
  operator to patch the SQLite DB or invent a new disarm command. If either
  count is nonzero here, the export was not taken from a quiescent source — stop,
  preserve everything (§15), and redo the export after resolving quiescence at
  the source.

## 14. Phase I — Start the destination daemon and verify normal load

Only after §12 postconditions hold and §13 reports both counts zero, start the
destination daemon using the actual documented procedure:

```bash
scripts/daemon.sh start
scripts/daemon.sh status    # expect "running (pid … , port …)"
```

`scripts/daemon.sh start` backgrounds `uv run python -m runtime.daemon` and
writes the pid/port files. Then verify **normal org discovery and service
health** using the existing documented surfaces:

```bash
happyranch orgs      # the relocated slug must appear with its root path
happyranch web       # verifies the daemon /api/v1/health is reachable
```

The relocated org is discovered by the normal existing behavior described in §2
— `org/teams.yaml` present, slug not reserved — and is loaded as a **normal
active org**. There is no inactive or non-operational state to claim: this
manual start is allowed only because the §13 zero-count gate passed and the
source org was quiescent when exported. Do **not** claim any automated
validation, fencing, rebind/rearm, archival automation, runtime admission, or
feature support that does not ship.

**Post-start checklist:**

- `scripts/daemon.sh status` reports running on the destination.
- `happyranch orgs` lists the relocated slug at the expected root.
- `happyranch web` (or `GET /api/v1/health`) succeeds.
- The destination `_pending/<operation-id>` directory is gone (consumed by §12).

## 15. Failure handling and escalation

**Before publication (during staging/validation):** retain the source intact and
clean **only** the exact `_pending/<operation-id>` operation directory you
created, after inspection, with both daemons stopped. Never `rm -rf` broadly,
and never touch `_pending` beyond your own operation directory. Re-run from the
top after fixing the cause.

**After publication but before start (e.g. a nonzero §13 count, or a failed
§12 postcondition):** do **not** overwrite, re-run, or merge the published tree,
and do **not** manually edit the DB. Leave the destination daemon **stopped**,
preserve the source intact, record the evidence (the §13 counts, `scripts/daemon.sh status`,
the §12 postcondition output), and escalate/reconcile through the supported
lifecycle controls — never through SQLite hand-editing or an invented disarm
command.

**Post-start:** verify org discovery and service health (§14) using existing
documented status/CLI surfaces. If the relocated org does not appear or the
health check fails, leave the destination stopped and escalate with the
recorded evidence.

This runbook makes **no automatic rollback claim**, no automatic-validation
claim, and no claim of runtime admission or fencing. It is a manual procedure;
every failure path stops, preserves source, and escalates.

**Source deletion** remains a **separate, later decision** made only after the
relocated org is published, started, and verified on the destination. This
runbook's success criteria do not include it.

## 16. Deferred: automated archive/import/activation (Slice B/C — out of scope)

The deferred automated product — archive/import/activation (Slice B) and
rebind/credential-transfer/rearm automation (Slice C) — is **not** implemented
by this runbook and is not shipped by the current runtime. This runbook is the
manual founder-operated restore, not that product. Credential transfer, rebind,
and rearm automation remain deferred; the mandatory §13 zero-count gate is what
makes this **manual** start allowed, and it is not a substitute for any deferred
automation.

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
- Full implementation design (for the deferred slices, not this runbook):
  `output/TASK-5426/final-offline-org-migration-design.md`.
