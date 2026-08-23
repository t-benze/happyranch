# Offline organization relocation — founder-operated staging runbook (THR-187)

> **Status:** manual runbook for the **staging phase only** of a one-time,
> founder-operated, maintenance-window move. It covers **export, transfer,
> verification, and private staging/validation of a candidate payload** — and
> then **stops before destination publication or destination-daemon start**.
> Publication, import admission, and activation are **deferred** to the
> separately reviewed Slice B and Slice C procedures (§13). It is **not** an
> authorization to build that automation, and it does **not** complete a
> relocation. The current `runtime/` head implements only **Slice A**
> (read-only preflight + founder-only zombie reconciliation, PR #680).
> Everything else below is a manual procedure performed with ordinary
> `sqlite3`, `tar`, and checksum tooling.
>
> Evidence for every statement here is the shipped code at the current head and
> the Step-0 evidence gate at
> `docs/superpowers/specs/org-portability-reference-consumers.md`.

## 1. Product boundary — what this procedure is and is not

This runbook prepares **one existing current-v2 org, under the same slug**, for
relocation into an absent destination slug on another (or the same) runtime,
during a founder-maintained offline window. It **exports** the org's portable
roots, **transfers** them over a secure channel, and **privately stages and
validates** a candidate payload on the destination — then **stops before
destination publication or destination-daemon start**.

It is **only** that staging work. It is **not** any of the following, and you
must not attempt any of them as part of this move:

- destination publication — placing the staged payload at the live
  `<dest-runtime>/orgs/<source-slug>` path (deferred **Slice B**, §13);
- starting the destination daemon after staging (a destination may only ever be
  started after a safely published, **admitted** target — neither ships today);
- a clone, a source deletion, or a merge/overwrite of two orgs;
- credential or daemon-token transfer;
- an online fence, a retry/receipt protocol, or an automatic rebind;
- automatic schedule rearm or any activation of the imported org (that is
  **Slice C**, a separately reviewed future procedure — §13);
- import admission / no-replace publication (that is **Slice B**, §13);
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

The source org root is `<source-runtime>/orgs/<source-slug>`; the **future**
destination publish target is `<dest-runtime>/orgs/<source-slug>` (same slug) —
this runbook stages a candidate payload but does **not** publish to it (§11).
The destination slug must be **absent** (§11).

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

Exactly one durable, target-only handoff record is defined for the future
import path. It is a regular file named
`org/.happyranch-imported-inactive.json`, and it is:

- **excluded on export** — never copied from a source org; if a source org
  somehow contains one, treat the presence as an anomaly and stop;
- **rejected on import** — any archive that supplies this path is refused, no
  matter what its manifest/digest says;
- **optionally constructed and validated by you in private destination staging**
  as a candidate handoff record (§11) — restrictive permissions (`chmod 600`),
  regular file, derived only from locally verified inputs — but **never
  published** by this runbook;
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

The current runtime does **not** read this marker at org admission: discovery
accepts any non-reserved slug whenever `org/teams.yaml` exists
(`RuntimeDir.iter_org_roots`), with **no** marker admission check. The
`imported_inactive` admission classifier that would honor this marker is a
deferred **Slice B** deliverable and does **not** ship today.

Because the marker is not enforced, it **cannot** keep a published org inactive
or non-operational, and it does **not** make a staged tree authoritative or
admitted. A staged marker is only a candidate handoff record for the future
Slice-B admission step. This is why the runbook stops before publication and
before any destination-daemon start (§11): a started destination would operate
the imported org regardless of any marker you staged.

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
only after a published, admitted, and validated destination exists (Slice B,
then §13); do nothing to the source org here. Leave the source daemon stopped
for the remainder of this runbook — no step restarts it.

## 10. Phase E — Transfer

Copy `org-archive.tar.gz` and `manifest.txt` to the destination machine over
your chosen **secure** channel. The archive is unsigned, unencrypted plaintext
(§2). Verify the archive SHA-256 on arrival matches the value you recorded:

```bash
shasum -a 256 org-archive.tar.gz   # must equal the recorded value
```

## 11. Phase F — Staging (verify → extract → validate → STOP before publication)

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

The slug must be absent including any symlink or broken entry. (This confirms
the future publish target is still clear; this runbook does **not** publish to
it.)

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

**5. Optionally construct the marker in staging — never publish it.** If you
want to retain a candidate handoff record for the future Slice-B admission
step, write the §5 JSON (slug + validated archive SHA-256 +
`state: imported_inactive`) as
`<dest-runtime>/orgs/_pending/<operation-id>/org/.happyranch-imported-inactive.json`,
`chmod 600`, regular file, then validate it parses and its `archive_sha256`
matches the recorded value. This staged marker is **not** published, does
**not** make the staged tree authoritative, and does **not** keep anything
inactive — the current runtime does not read it (§5).

**6. STOP before destination publication and before destination-daemon start.**
This runbook ends here. There is **no publication step and no destination
start**: a destination daemon may only ever be started after a safely
published, **admitted** target, and neither the Slice-B import-admission nor
the no-replace publication capability ships today (§13). Do **not** use
`mkdir "$DST"` then move contents, do **not** use `mv`/`cp -r` onto a
pre-existing path, and do **not** use `os.replace` — there is no documented
atomic no-replace publication in this runbook, and inventing one risks a
partially populated, discoverable org. Leave both daemons stopped and the
staged payload inside its own `_pending/<operation-id>` directory. Proceed to
§12 for the bounded post-staging cleanup and escalation rules.

## 12. Phase G — Post-staging: bounded cleanup, escalation, and end state

There is **no publication step** in this runbook, so there is no
post-publication state to reconcile and the staged tree is **not**
authoritative. Every failure or inconsistency is a **staging failure**: there
is **no retry and no overwrite**. Inspect and clean **only** the exact
operation staging path you created
(`<dest-runtime>/orgs/_pending/<operation-id>`), after inspection, with both
daemons stopped. Never `rm -rf` broadly, and never touch `_pending` beyond your
own operation directory. Re-run from the top after fixing the cause.

If the staged candidate payload validates but you cannot proceed to publication
because Slice B has not shipped (the normal end of this runbook), **escalate**
— do not improvise a publication command, and do not start either daemon. There
is no invented platform command for atomic no-replace publication in this
runbook, and none is documented until the separately reviewed Slice-B
capability lands.

This runbook makes **no automatic rollback claim** and does **not** complete a
relocation: the org is exported and staged, but not published, not admitted, and
not activated. The source is preserved intact; both daemons remain stopped.

**Source deletion** is a separate, later decision made only after a published,
admitted, and validated destination exists (Slice B, then §13). It is out of
scope for this runbook's success criteria.

## 13. Deferred: publication, admission, and rearm (Slice B then Slice C — out of scope)

The one-way ordering is: a destination daemon may **only** ever be started after
a **safely published, admitted** target exists. Neither precondition ships
today, so **no destination start occurs in this runbook** and the staged org is
**not** an imported, authoritative org.

- **Slice B (import admission + no-replace publication)** — a separately
  reviewed future deliverable that performs the atomic, no-replace publication
  of a staged payload into the live destination tree **and** enforces the
  `imported_inactive` marker at org admission, so a published imported org is
  actually refused/non-operational until rearmed. Until Slice B ships, do
  **not** publish and do **not** start.
- **Slice C (rebind and rearm)** — a separately reviewed future procedure that,
  after admission, validates and rebinds all activation dependencies (layout,
  marker/slug/hash, reference-map fixtures, destination-local bindings and
  credentials, executor configuration, and an explicit schedule-rearm plan) and
  then **explicitly rearms** eligible schedules before removing the marker.

The imported org must remain non-operational throughout. Do **not** treat
`schedules.active=0` as a safety control — it is inert receipt data, not a
firing control, and this runbook does not mutate it. The safety mechanism is
shipped Slice-B admission keeping an imported org non-operational, plus this
runbook's hard stop before publication; the marker file alone is not a control
(§5). If admission, validation, or rearm fails, the org stays non-operational
and the destination daemon stays stopped.

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
