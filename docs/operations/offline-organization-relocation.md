# Offline organization relocation — founder-operated runbook (THR-187)

> **Status:** manual runbook for a **one-time, founder-operated, maintenance-window
> move**. It is **not** the deferred archive/import/activation product (THR-187
> Slice B/C), and it is not an authorization to build that automation. The
> current `runtime/` head implements only **Slice A** (read-only preflight +
> founder-only zombie reconciliation, PR #680). Everything else below is a manual
> procedure performed with ordinary `sqlite3`, `tar`, and checksum tooling.
>
> Evidence for every statement here is the shipped code at the current head and
> the Step-0 evidence gate at
> `docs/superpowers/specs/org-portability-reference-consumers.md`.

## 1. Product boundary — what this procedure is and is not

This runbook relocates **one existing current-v2 org, under the same slug, into
an absent destination slug** on another (or the same) runtime, during a
founder-maintained offline window.

It is **only** that. It is **not** any of the following, and you must not
attempt any of them as part of this move:

- a clone, a source deletion, or a merge/overwrite of two orgs;
- credential or daemon-token transfer;
- an online fence, a retry/receipt protocol, or an automatic rebind;
- automatic schedule rearm or any activation of the imported org (that is
  **Slice C**, a separately reviewed future procedure — §13);
- a v0/v1 layout conversion (every non-current-v2 layout is a named refusal,
  never an auto-upgrade).

## 2. Security and data-safety warnings (read before touching anything)

- **Archives are unsigned, unencrypted local plaintext.** The `.tar.gz` produced
  by this procedure carries your org's DB and KB/talks/memory in plaintext. You
  are responsible for choosing a **secure transfer channel** (e.g. an encrypted
  copy over a trusted transport). The archive file hash in §9 is integrity
  evidence, not authentication or confidentiality.
- **Never copy a whole org folder or a whole workspace tree.** Workspace trees
  (`workspaces/<agent>/...`) can contain **test-harness daemon tokens** and other
  credentials materialized during bootstrap. Copying them wholesale can exfiltrate
  tokens. Only the classifier-approved roots in §4 are carried, and even there the
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

## 3. Prerequisites

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
destination slug must be **absent** (§11).

## 4. Portable roots — the allow-list (derived from the shipped classifier)

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
| `org/` | whole tree, **excluding** any `org/.happyranch-imported-inactive.json` (§5) |
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

Explicitly **never** carried: the marker (§5), `happyranch.db-wal` /
`happyranch.db-shm`, generated markers (`.hr_review_renamed`,
`.org_settings_seeded`), `dashboard_projection.json`, caches (`.pytest_cache`,
`.DS_Store`), legacy residue DBs (`audit.db`, `db.sqlite3`), `workspaces/*/output`,
and every other `workspaces/*` subtree except `memory`. Any direct child the
classifier does not explicitly allow or explicitly exclude is a **rejection**
— stop, do not guess.

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

## 5. The inactive marker (`org/.happyranch-imported-inactive.json`)

Exactly one durable, target-only handoff record exists. It is a regular file
named `org/.happyranch-imported-inactive.json`, and it is:

- **excluded on export** — never copied from a source org; if a source org
  somehow contains one, treat the presence as an anomaly and stop;
- **rejected on import** — any archive that supplies this path is refused, no
  matter what its manifest/digest says;
- **created by you, in private destination staging, immediately before publish**
  (§11), restrictive permissions (`chmod 600`), regular file, derived only from
  locally verified inputs;
- **never source authority** — it is destination handoff evidence, not portable
  source data.

Content (single JSON object):

```json
{
  "format_version": 1,
  "slug": "<destination-slug>",
  "archive_sha256": "<validated archive SHA-256 from §9>",
  "state": "imported_inactive"
}
```

The current runtime does **not** yet read this marker; the `imported_inactive`
admission classifier that honors it is a deferred Slice B deliverable. Until
that lands, the marker is a signal to every human operator and to the future
rearm procedure that this org is imported and must stay non-operational (§13).

## 6. Phase A — Deploy and calibrate Slice-A preflight on both instances

Slice A (PR #680) must be **deployed and running**, not merely merged.

1. On each instance, confirm the running daemon is built from a head that
   includes Slice A, then restart it so the new routes are live:
   `scripts/daemon.sh status` (calibrate the actual stop/start against the real
   daemon), then restart per your normal deploy procedure. **Merge ≠ live
   deployment** — do not proceed until both daemons actually serve the
   `/portability-preflight` route.
2. Calibrate on the **source** instance:

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
to the allow-list in §4 — never by guessing a new portable root.

## 8. Phase C — Stop both daemons and verify stopped

On the source and destination instances:

```bash
scripts/daemon.sh stop --force
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

Verify `status` reports stopped on **both** instances before the next phase.

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

**4. Build the allow-listed payload.** Copy **only** the §4 roots into
`$STAGE/org-payload/`, including the staged snapshot as `happyranch.db`. For the
`org/` root, exclude any `org/.happyranch-imported-inactive.json`. Exclude every
sidecar, marker, cache, and non-allow-listed workspace subtree. Then reject any
member that is a symlink, dangling link, or nonregular file/dir/device/FIFO at
any depth:

```bash
find "$STAGE/org-payload" \( -type l -o -type p -o -type s -o -type b -o -type c \) -print
# non-empty output ⇒ stop: remove the offending member from the source org
# (per §4) and re-run preflight; never "fix" it in the payload
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
only after destination validation succeeds (§12); do nothing to the source org
here. Leave the source daemon stopped until you have finished validating the
destination.

## 10. Phase E — Transfer

Copy `org-archive.tar.gz` and `manifest.txt` to the destination machine over
your chosen **secure** channel. The archive is unsigned, unencrypted plaintext
(§2). Verify the archive SHA-256 on arrival matches the value you recorded:

```bash
shasum -a 256 org-archive.tar.gz   # must equal the recorded value
```

## 11. Phase F — Import (verify → stage → validate → publish → marker)

Run this on the destination machine. `$DST=<dest-runtime>/orgs/<source-slug>`;
staging is `<dest-runtime>/orgs/_pending/<operation-id>` (the `_pending` name is
reserved and skipped by the runtime's org enumeration, so it is never treated as
an org). `_pending` is crash residue only — **not** a retry protocol.

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
- any `org/.happyranch-imported-inactive.json` (archive injection of the marker
  is a refusal);
- any member not in the §4 allow-list.

Verify the allow-list of **every** member against §4 and the recorded manifest.

**4. Validate the staged DB and reference map.** Run
`PRAGMA integrity_check` (expect `ok`) and `PRAGMA foreign_key_check` (expect no
rows) against the staged `happyranch.db`. Then, as the manual form of the
deferred reference validation, confirm each DB-held filesystem reference in the
Step-0 map (C1–C13) resolves to a staged regular file with no symlink/escape;
treat any missing, escaping, symlinked, or data-shaped refusal (C12b/C12c/C13)
as a stop. If you cannot confirm a consumer resolves, escalate — do not guess.

**5. Publish with a same-filesystem, no-replace primitive.** Staging
(`_pending/`) is on the destination filesystem, a sibling of the publish target,
so publication is a same-filesystem operation. The no-replace primitive is
`mkdir`, which fails atomically if the name already exists in any form:

```bash
mkdir "$DST"                     # EEXIST ⇒ stop; the name is now reserved
# then rename the staged tree's contents into the freshly-created, empty dir
```

Do **not** use `mv`/`cp -r` onto a pre-existing path, do **not** use
`os.replace`, and do **not** use an overwrite fallback. If `mkdir` fails, the
slug is not absent — inspect and stop.

**6. Create the marker in staging, then publish it with the tree.** Write the
§5 JSON (slug + validated archive SHA-256 + `state: imported_inactive`) as
`$DST/org/.happyranch-imported-inactive.json`, `chmod 600`, regular file —
before the tree is considered published. (In the deferred automation this is
generated in staging immediately before publish; in the manual run it must be
present before you start the destination daemon.)

**7. Start the destination daemon only after publish:**

```bash
scripts/daemon.sh start
```

## 12. Phase G — Validation and failure handling

**Pre-publication failure** (anything before §11 step 5): there is **no retry
and no overwrite**. Inspect and clean **only** the exact operation staging path
you created (`<dest-runtime>/orgs/_pending/<operation-id>`), with both daemons
stopped. Never `rm -rf` broadly, and never touch `_pending` beyond your own
operation directory. Re-run from the top after fixing the cause.

**Post-publication failure or inconsistency**: the destination is now
**authoritative**. Do not rerun, merge, or remove. Verify the marker parses as
valid and its `archive_sha256` matches the value you recorded, and that
admission (§5) would classify it `imported_inactive`. If anything is
inconsistent, **escalate** — do not "fix" it by deleting or rewriting the
published tree. This runbook makes **no automatic rollback claim**.

**Source deletion** is a separate, later decision made only after the
destination has been validated. It is out of scope for this runbook's success
criteria.

## 13. Deferred: rebind and rearm (Slice C — out of scope)

The imported org **remains inactive and non-operational**. Schedules on the
imported org must remain non-operational until a **separately reviewed**
procedure (Slice C) validates and rebinds all activation dependencies (layout,
marker/slug/hash, reference-map fixtures, destination-local bindings and
credentials, executor configuration, and an explicit schedule-rearm plan) and
then **explicitly rearms** eligible schedules before removing the marker.

Do **not** treat `schedules.active=0` as a safety control — it is inert receipt
data, not a firing control, and this runbook does not mutate it. The safety
mechanism is that the imported root carries the inactive marker and is not
operated. If validation or rearm fails, the marker stays and the org stays
inactive.

## Source-of-truth references

- Classifier: `runtime/portability/roots.py` (`classify_root_entries`).
- Eligibility: `runtime/portability/eligibility.py` (`compute_eligibility`).
- Preflight/reconcile routes: `runtime/daemon/routes/portability.py`
  (`GET /portability-preflight`, `POST /reconcile-portability`).
- Daemon lifecycle: `scripts/daemon.sh`.
- Runtime layout + reserved slugs: `runtime/runtime.py`
  (`RuntimeDir.iter_org_roots`, `_RESERVED_ORG_SLUGS`).
- DB-to-filesystem reference map: `docs/superpowers/specs/org-portability-reference-consumers.md`.
- Full implementation design (for the future slices, not this runbook):
  `output/TASK-5426/final-offline-org-migration-design.md`.
