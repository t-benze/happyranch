# Offline organization relocation — founder-operated manual runbook (THR-187 / GH-709 Slice A)

> **Status:** a **manual runbook** for a **one-time, founder-supervised,
> same-slug, offline maintenance-window** move of one or more existing
> current-v2 orgs into **absent** destination slugs on another runtime
> (GH-709 Slice A hardening). It is **not** the deferred automated
> archive/import/activation product (Slice B/C), it implements no automation,
> and it makes **no claim** that the shipped runtime enforces any
> inactive/admission state, rebind, or schedule-rearm gate. The shipped runtime
> carries only **Slice A** (read-only preflight + founder-only zombie
> reconciliation).
>
> **GH-709 Slice A fixes in this revision:** a `COPYFILE_DISABLE=1` macOS
> archive recipe (contract-tested; real AppleDouble suppression must be
> verified on a real macOS machine — §4), retaining rejection of any `._*`
> member; staged-DB validation through `immutable=1` read-only URI opens that
> create no `-wal`/`-shm`; an exact transfer-operation artifact gate (legitimate
> `artifacts/**/*.tar.gz` evidence passes); a founder decision on terminal
> historical job records (retained, never mutated, streams not transported); a
> loader-backed multi-org inventory with a durable per-org operation ledger;
> a mandatory post-publication agent-readiness gate; destination launch
> diagnostics with an accurately bounded daemon-child CLI-parity limitation
> (the shipped runtime has no daemon-child diagnostic seam — see §7.2).
>
> **Honesty boundary (binding).** Slices B/C *runtime* guarantees are **not
> shipped** by the runbook itself (an automated importer, online transfer
> fences, batch automation, an exhaustive readiness command); Slice D is
> entirely unshipped. What IS shipped in the runtime: Slice B ships
> active-roster-only bulk ``init-agent`` targeting; Slice C ships
> executor-profile-specific readiness-marker verification — ``init-agent``
> reports ``done`` only after the selected executor profile's exact readiness
> marker exists as a valid regular file produced by the bootstrap (see §7.1
> steps 2–3). The multi-org inventory, per-org operation ledger, remaining
> readiness checks, and diagnostics below are **operator-enforced**: the
> founder runs them manually and records the results; nothing else in the
> shipped runtime verifies them. Every command below is an existing, verified
> surface; none is invented.
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
refusal, never an auto-upgrade); an automatic importer or first-class
relocation tool (no automated importer exists; Slice B shipped only
active-roster bulk init-agent targeting and Slice C only init-agent
readiness-marker verification — see the honesty boundary above); a
batch-atomic protocol (each org publishes independently, a later
failure never rolls back an already-published org, and no successful org
masks a failure — §1.1, §8); or credential/token-residue inspection or
cleanup (a separate, founder-authorized task; do **not** combine destructive
cleanup with this relocation).

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
| `INBOX` | destination **receive** dir, e.g. `$DST_RUNTIME/orgs/_pending/$OP.inbox/` — absolute, founder-private (`chmod 700`), **outside** both `$STAGE` and `$DST`; holds `org-archive.tar.gz`, `manifest.txt`, and the recorded `archive.sha256` receipt (§5) |
| `HR_CHECKOUT` | absolute path to the **version-matched** HappyRanch checkout used to deploy the destination daemon — the checkout whose code (including `runtime/portability/roots.py`) matches the running destination deployment. Its **supported environment** is uv (`pyproject.toml` requires-python `>=3.12,<3.15`; `uv.lock` pinned). Used **only** for the offline classifier gate (§5 step 7); it is **not** the live daemon and serves no route |
| `LEDGER` | per-org operation ledger path (§1.1), e.g. `$DST_RUNTIME/orgs/_pending/<batch>-ledger.md`; records per-org phase, evidence, and founder-approved exceptions (including observed runbook-created source sidecar residue, §3 — left in place, never deleted by this runbook) |

Hard requirements:

- **Absolute, resolved, non-symlink paths.** Resolve with `cd <dir> && pwd -P`
  (macOS `readlink -f` is not portable). Reject any path that is a symlink or
  contains a symlink component.
- **Founder-private, same-filesystem destination staging** under
  `$DST_RUNTIME/orgs/_pending/$OP`. `_pending` is a reserved slug
  (`runtime/runtime.py::_RESERVED_ORG_SLUGS`) skipped by org enumeration, so it
  is never treated as an org. It is crash residue, **not** a retry protocol.
- **Receive inbox outside the candidate tree.** The transferred archive,
  manifest, and recorded hash live in a founder-private `$INBOX` that is
  **outside both `$STAGE` and `$DST`**; they are never copied into `$STAGE`
  (§5, §6).
- **Both daemons stopped** during publication (§6) and before any source SQLite
  open (§3).
- **Real-classifier gate before publish.** A spelling/type screen is **not** a
  classifier. The staged candidate must pass the shipped runtime classifier
  `runtime/portability/roots.py::classify_root_entries` — invoked **offline**
  from the version-matched `HR_CHECKOUT` (§5 step 7) — with **zero rejections**
  before any publication. The destination daemon stays stopped; the online
  `/portability-preflight` route is a **source-only** seam and cannot run
  against the stopped destination.

### 1.1 Loader-backed org inventory and the per-org operation ledger (multi-org batches)

The runbook below is written single-org, but a real runtime may hold several
orgs. **Never inventory by globbing directories under `$SRC_RUNTIME/orgs/`**:
reserved and non-org directories (`_pending`, `_archive`, `worktrees`,
`_pending/$OP`, `_pending/$OP.inbox`) are not loadable orgs even though they
are direct children. Inventory through the **same loader the runtime uses**,
`RuntimeDir.iter_org_roots` (`runtime/runtime.py`): a loadable org is a
slug-matching directory (`^[a-z0-9-]{1,40}$`) that is not `_pending`/`_archive`
and that contains `org/teams.yaml`. Run this offline from the version-matched
`HR_CHECKOUT` (both daemons may be stopped):

```bash
set -euo pipefail
cd "$HR_CHECKOUT" && uv run python - "$SRC_RUNTIME" <<'PY'
import sys
from pathlib import Path
from runtime.runtime import RuntimeDir
rt = RuntimeDir.load(Path(sys.argv[1]))
for slug, root in sorted(rt.iter_org_roots()):
    print(f"{slug}  {root}")
PY
```

Cross-check while the source daemon is up with `happyranch orgs` (§2 step 1
window); the daemon's `/api/v1/orgs` route lists the same loadable set. Treat
any direct child the loader does **not** yield as **not an org** — leave it
untouched (e.g. a sibling `worktrees/` directory stays in place).

**Per-org operation identity and ledger.** For a batch, give **each org its
own** operation id and paths: `OP_<slug>` (e.g. `2026-08-25-thr187-family`),
`STAGE_<slug>` = `$DST_RUNTIME/orgs/_pending/$OP_<slug>`, and
`INBOX_<slug>` = `$DST_RUNTIME/orgs/_pending/$OP_<slug>.inbox`. Every org runs
§3–§6 independently: unique export, transfer, stage, validation, and publish
decision. **Maintain one durable per-org operation ledger** at a founder-private
path, e.g. `$DST_RUNTIME/orgs/_pending/<batch>-ledger.md` (under the reserved
slug, so it is never enumerated as an org). For every org, record a row with:
slug, `OP_<slug>`, current phase (`inventory → exported → validated →
published → zero-gated → started → ready` or `blocked`), the evidence paths
(archive receipt, staged manifest diff, classifier output, zero-count output),
and any **founder-approved exception** (e.g. terminal historical job paths,
§4/§5 step 9). Update the ledger at **every** phase transition, including
failures. The ledger is the operator-enforced record the brief/issue require:
- each org validates and publishes **independently** — a later org's failure
  never rolls back an org already published;
- source copies and operation evidence stay in place for every org (failed or
  not) — never delete them during the batch (§8);
- a failing org is recorded `blocked` with evidence and never masked by a
  sibling's success: the destination starts only after **every requested org**
  is either `published` + `zero-gated` or explicitly `blocked` with recorded
  evidence and a founder decision to proceed without it (§7).

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
   set -euo pipefail
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
set -euo pipefail
scripts/daemon.sh stop --force     # default daemon home requires --force
if scripts/daemon.sh status; then
  echo "daemon still running — STOP" >&2
  exit 1
fi
echo "daemon confirmed not running (status exited non-zero as expected)"
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
set -euo pipefail
if test -e "$SRC/happyranch.db-wal" || test -e "$SRC/happyranch.db-shm"; then
  echo "SIDECAR PRESENT — STOP: either sidecar prevents the logical snapshot" >&2
  exit 1
fi
echo "no sidecars — logical snapshot may proceed"
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

**2. Logical snapshot (WAL-aware source read; no source data mutation).** Take
the snapshot with the stdlib `sqlite3` backup API from the version-matched
`HR_CHECKOUT` (no CLI dependency, and the destination is forced to
rollback-journal mode so **no `-wal`/`-shm` is ever created at `$STAGE`**):

```bash
set -euo pipefail
# Re-run the §3 step-1 sidecar gate immediately before opening the source DB:
# either present sidecar exits nonzero and prevents the logical snapshot.
if test -e "$SRC/happyranch.db-wal" || test -e "$SRC/happyranch.db-shm"; then
  echo "SIDECAR PRESENT — STOP: do not open SQLite" >&2
  exit 1
fi
cd "$HR_CHECKOUT" && uv run python - "$SRC/happyranch.db" "$STAGE/happyranch.db" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1], sys.argv[2]
reader = sqlite3.connect(f"file:{src}?mode=ro", uri=True)   # WAL-aware read
writer = sqlite3.connect(dest)
writer.execute("PRAGMA journal_mode=DELETE")                # no -wal/-shm at stage
reader.backup(writer)
writer.execute("PRAGMA journal_mode=DELETE")                # re-assert after header copy
writer.close()
reader.close()
PY
# The read-only SOURCE reader can itself create a -wal/-shm pair beside the
# source (a SQLite read-only WAL reader initializes WAL shared memory —
# verified; this is runbook-created residue, not evidence of live access). The
# pre-open gate proved neither existed, so any sidecar present NOW was created
# by this very command in the exclusive stopped-daemon window. Slice A
# OBSERVES and records only — it never deletes source sidecars: cleaning them
# is destructive cleanup, a separate founder-authorized decision outside this
# runbook's scope (§8). Record the observation in the operation ledger and
# leave the files in place.
if test -e "$SRC/happyranch.db-wal" || test -e "$SRC/happyranch.db-shm"; then
  echo "runbook-created source sidecar after backup (pre-gate proved none existed);" \
    "recorded in ledger, left in place — NOT removed by this runbook (§8)" \
    | tee -a "$LEDGER" >&2
fi
```

`$LEDGER` is the per-org operation ledger path (§1.1); for a single-org move
use any founder-private path. Slice A **never deletes source files**: a
pre-existing sidecar blocks the runbook and stays untouched (§3 step 1), and
sidecar residue created by the runbook's own read is recorded in the ledger and
left in place — removing it is a separate founder-authorized
**destructive-cleanup decision** (§8), never performed by this runbook.

(There is no fictional `happyranch export` command; `sqlite3.Connection.backup`
is the real primitive.)

**3. Validate the staged snapshot (immutable read-only, no sidecars).** The
staged file is the **completed logical snapshot** — the `sqlite3 .backup` API
folds every committed row into the main file, so `$STAGE/happyranch.db` is
self-contained and has no WAL of its own. Validate it with a URI `immutable=1`
read-only open, which **never creates `-wal`/`-shm`** (an ordinary or `mode=ro`
open can create sidecars; the repo's stale-job observer records this property).
Immutable reads only the main file, so it is safe **only** because the snapshot
is complete — **immutable must never replace the WAL-aware source backup**
in §3 step 2 (the source may hold committed-but-uncheckpointed WAL frames that
immutable would silently miss). Ordering is strict: prove no pre-existing
candidate sidecars first, validate, then prove none were created:

```bash
set -euo pipefail
# 1. prove no pre-existing sidecars beside the staged candidate
if test -e "$STAGE/happyranch.db-wal" || test -e "$STAGE/happyranch.db-shm"; then
  echo "STAGED SIDECAR PRESENT — STOP: do not open the staged DB" >&2
  exit 1
fi
# 2. GH-709 Slice A: checked immutable staged-DB validation (assert exactly
#    ok / empty FK). The helper exits nonzero unless PRAGMA integrity_check
#    returns exactly ["ok"] AND PRAGMA foreign_key_check returns no rows — so a
#    corrupt or FK-invalid candidate exits nonzero here and publication is
#    unreachable (every later command runs under set -e). Stdlib only (no
#    sqlite3 CLI dependency); immutable=1 read-only URI creates no -wal/-shm.
cd "$HR_CHECKOUT" && uv run python - "$STAGE/happyranch.db" <<'PY'
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
except sqlite3.Error as exc:
    print(f"CANNOT OPEN STAGED DB: {exc}", file=sys.stderr)
    sys.exit(1)
try:
    integrity = conn.execute("PRAGMA integrity_check;").fetchall()
except sqlite3.DatabaseError as exc:
    print(f"INTEGRITY_CHECK ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
if integrity != [("ok",)]:
    print(f"INTEGRITY_CHECK NOT OK: {integrity!r}", file=sys.stderr)
    sys.exit(1)
try:
    fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
except sqlite3.DatabaseError as exc:
    print(f"FOREIGN_KEY_CHECK ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
if fk:
    print(f"FOREIGN_KEY_VIOLATIONS: {fk!r}", file=sys.stderr)
    sys.exit(1)
conn.close()
print("staged DB valid: integrity_check exactly ok, foreign_key_check empty")
PY
# 3. prove the validation created no sidecars (no self-induced residue)
if test -e "$STAGE/happyranch.db-wal" || test -e "$STAGE/happyranch.db-shm"; then
  echo "VALIDATION CREATED A SIDECAR — STOP: investigate before continuing" >&2
  exit 1
fi
echo "staged snapshot valid; no sidecars created"
```

The checked helper above is the **complete executable path** — a stdlib
`sqlite3` URI `immutable=1` open with exact assertions; it is **not** a
fallback and needs no `sqlite3` CLI. An ordinary or `mode=ro` open can create
sidecars (the repo's stale-job observer records this property), so this
runbook never uses them on the staged snapshot.

## 4. Build the allow-listed manifest and archive (portable roots only)

Carry **only** what the shipped classifier `runtime/portability/roots.py::
classify_root_entries` approves. It is **not** the bare `ALLOWED_ROOTS` set:
`skills/` and `workspaces/` are special-cased there, so iterating
`ALLOWED_ROOTS` alone would silently drop agent memory and valid legacy skills.

Portable roots: the logical `happyranch.db` snapshot (not a raw copy); `org/`
(whole tree); `artifacts/`, `kb/`, `threads/`, `task-attachments/`, `dreams/`,
`work_hours/`, `schedules/`, `talks/`; `skills/` only where each package passes
the classifier's legacy-skill validation; and `workspaces/<agent>/memory/**`
**only**. **`jobs/` is deliberately not carried** — see the terminal-job policy
below.

**Terminal historical job records (founder policy, GH-709).** Job rows live in
the snapshot `happyranch.db` and **travel inside it, retained untouched**: no
row is mutated, no `stdout_path`/`stderr_path`/`cwd_hint` value is rewritten,
and there is no importer/rebase in the shipped runtime (Slice B/C). The
**machine-local stream files** (`jobs/JOB-NNN.out|err` — the stdout/stderr
bytes) are **not transported**: `jobs/` is excluded from the payload below.
Because the retained rows still hold source-absolute stream paths, historical
stream links (`happyranch jobs output <id>` / `happyranch jobs tail <id>` on
terminal rows) **may be unavailable (empty) after relocation** — documented,
expected, and not a gate failure (§5 step 9). This is safe because the §2
preflight requires **zero pending/running jobs** before export, so every
carried job row is terminal (`completed`/`failed`/`rejected`), and only
`pending` rows are launchable — a terminal row can never be re-launched, so a
legacy `cwd_hint` value is never executed on the destination.

Never carried: `happyranch.db-wal`/`-shm`; generated markers
(`.hr_review_renamed`, `.org_settings_seeded`); `dashboard_projection.json`;
caches (`.pytest_cache`, `.DS_Store`); legacy residue DBs (`audit.db`,
`db.sqlite3`) unless zero-byte-and-excluded by the classifier; the `jobs/`
directory (machine-local stdout/stderr streams, §4 terminal-job policy); every
`workspaces/*` subtree except `memory` (including `output`, `repos`,
bootstrap/settings); and any unknown or nonregular entry. **Any direct child
the classifier does not explicitly allow or explicitly exclude is a rejection
— stop, do not guess.** (The shipped classifier still allow-lists `jobs/` as a
whole-tree root; this runbook's §4 recipe simply does not copy it. An archive
that nonetheless contains `jobs/` members carries inert bytes whose stored
absolute-path links will not resolve on the destination — record it in the
ledger and investigate, but do not treat the bytes as a portable resource.)

Copy the allow-listed roots into `$STAGE/org-payload/` (place the snapshot as
`org-payload/happyranch.db`). For the memory-only carve-out, use a
`find`-based selection rather than rsync filters — macOS ships `openrsync`,
whose `--include`/`--exclude` semantics differ from GNU rsync, so a portable
`find` + `cp -R` loop is safer:

```bash
set -euo pipefail
mkdir -p "$STAGE/org-payload"
# copy each whole-tree portable root with cp -R — the allow-listed set from
# the paragraph above, EXCLUDING jobs/ (terminal-job policy) and workspaces/
# (memory carve-out handled by the loop below): org, artifacts, kb, threads,
# task-attachments, dreams, work_hours, schedules, talks, skills (valid pkgs)
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
set -euo pipefail
find "$STAGE/org-payload" \( -type l -o -type p -o -type s -o -type b -o -type c \) -print
# non-empty output ⇒ STOP: remove the offending member from the SOURCE org and
# re-run preflight; never "fix" it in the payload
```

**Record a verifiable manifest + checksums**, then archive and hash it:

```bash
set -euo pipefail
(cd "$STAGE/org-payload" && find . -type f -print0 | LC_ALL=C sort -z | \
  xargs -0 shasum -a 256) > "$STAGE/manifest.txt"        # macOS
# Linux: replace shasum -a 256 with sha256sum
# macOS/BSD tar: COPYFILE_DISABLE=1 suppresses synthetic AppleDouble ._* members
# emitted from extended attributes (xattrs) of copied files. On Linux GNU tar
# ignores the variable — harmless. Do NOT drop it on macOS. (Real AppleDouble
# suppression is a macOS behavior; verify the recipe on a real macOS machine
# before relying on it — the runbook's member screen in §5 still rejects any
# injected ._* member, which is the authoritative fail-closed backstop.)
export COPYFILE_DISABLE=1
tar -czf "$STAGE/org-archive.tar.gz" -C "$STAGE/org-payload" .
shasum -a 256 "$STAGE/org-archive.tar.gz" | awk '{print $1}' > "$STAGE/archive.sha256"
# archive.sha256 = the 64-char hex digest; transfer it with the archive + manifest (§5)
```

**The source is preserved fully** — nothing here mutates or deletes it.

## 5. Receive, inspect, extract, and validate on the destination

Transfer `org-archive.tar.gz` and `manifest.txt` over a founder-selected
**secure** channel. The archive is unsigned, unencrypted local plaintext; its
hash is integrity evidence, not authentication or confidentiality.

**1. Receive into a private inbox outside the candidate tree.** Create a fresh
founder-private **inbox** that sits **outside both `$STAGE` and `$DST`** — for
example `$DST_RUNTIME/orgs/_pending/$OP.inbox/` (under the reserved `_pending`
slug, so it is never enumerated as an org) — and receive the archive, the
manifest, and the recorded archive SHA-256 there. **Never copy any of these
three artifacts into `$STAGE`**; they are transfer evidence, not portable org
data (§6):

```bash
set -euo pipefail
INBOX="$DST_RUNTIME/orgs/_pending/$OP.inbox"
mkdir -p "$INBOX" && chmod 700 "$INBOX"
# receive (scp / rsync / founder-selected channel) into "$INBOX":
#   org-archive.tar.gz , manifest.txt , archive.sha256
```

`archive.sha256` is the single-line hex digest recorded in §4.

**2. Destination stopped, slug absent** (including any symlink/broken entry):

```bash
set -euo pipefail
if scripts/daemon.sh status; then
  echo "destination daemon still running — STOP" >&2
  exit 1
fi
echo "destination daemon confirmed not running"
if test -e "$DST" || test -L "$DST"; then
  echo "DST SLUG PRESENT — STOP (must be absent, including symlink/broken entry)" >&2
  exit 1
fi
echo "slug absent"
```

**3. Verify the received archive hash against the source-recorded receipt:**

```bash
set -euo pipefail
if test "$(shasum -a 256 "$INBOX/org-archive.tar.gz" | awk '{print $1}')" \
    != "$(cat "$INBOX/archive.sha256")"; then
  echo "HASH MISMATCH — STOP: received archive does not match the source-recorded digest" >&2
  exit 1
fi
echo "archive hash matches"
# Linux: replace shasum -a 256 with sha256sum
```

**4. Inspect every archive member BEFORE extraction — fail closed.** A `tar`
listing is an *inspection aid*, **not** a safe extractor, and this screen is a
**preliminary** spelling/type screen, **not** the classifier: it cannot judge
skills-package shape or the type of `workspaces/<agent>/memory`. The shipped
runtime classifier does that on the extracted candidate in step 7. The screen
below must pass **before** any `tar -x` runs; every rejection below **exits
nonzero**, so extraction is never reached.

```bash
set -euo pipefail
tar -tzf "$INBOX/org-archive.tar.gz" > "$INBOX/members.txt"         # member names
tar -tvzf "$INBOX/org-archive.tar.gz" > "$INBOX/members-typed.txt"  # typed listing; leading char = member type
# Normalize: strip the leading "./" and a trailing "/", drop the bare root entry.
# An empty member list (only the bare ./ root) makes grep exit 1 — allowed here
# because the EMPTY ARCHIVE check below rejects that case explicitly.
grep -vE '^\./?$' "$INBOX/members.txt" | sed 's#^\./##; s#/$##' > "$INBOX/members-norm.txt" || :
```

Reject the archive — do **not** extract — if **any** of these hold:

- **empty**: `members-norm.txt` is empty (no members beyond the `./` root);
- **absolute path**: a member name begins with `/`;
- **`..` traversal**: a member name contains a `..` path component;
- **duplicate path**: a member name appears more than once;
- **nonregular entry**: a `members-typed.txt` line does **not** begin with `d`
  (directory) or `-` (regular file) — this rejects symlinks (`l`), hardlinks
  (`h`), block/char devices (`b`/`c`), FIFOs (`p`), and sockets (`s`);
- **AppleDouble `._*` member**: a member basename begins with `._` — synthetic
  macOS metadata emitted from extended attributes. §4 suppresses it with
  `COPYFILE_DISABLE=1`, but this screen must still **reject any injected
  `._*` member** (defense in depth; the rejection below is the authoritative
  fail-closed backstop, independent of the tar version used);
- **unallowlisted member**: a member path is not under one of the §4 portable
  roots (`happyranch.db`, `org/`, `kb/`, `talks/`, `threads/`,
  `task-attachments/`, `jobs/`, `dreams/`, `work_hours/`, `schedules/`,
  `artifacts/`, `skills/`, `workspaces/<agent>/memory/**`);
- **smuggled transfer/inbox artifact**: a member named `org-archive.tar.gz`,
  `manifest.txt`, `archive.sha256`, `members.txt`, `members-typed.txt`, or
  `staged-manifest.txt` appears anywhere in the archive.

The screen is deliberately **name/type exact**, not extension-wide: a
legitimate evidence bundle `artifacts/evidence/report.tar.gz` passes every
check (it is not one of the six operation filenames and is under an allowed
root), while `org-archive.tar.gz` or `manifest.txt` at any depth is rejected
as a smuggled transfer artifact. `jobs/` members are still allowed here (the
shipped classifier allow-lists `jobs/`); per §4's terminal-job policy this
runbook does not transport `jobs/` itself, so an archive that contains them
was not produced by §4 — note it in the operation ledger (§1.1).

```bash
set -euo pipefail
failed=0
: > "$INBOX/rejections.txt"
if ! test -s "$INBOX/members-norm.txt"; then echo "EMPTY ARCHIVE" >> "$INBOX/rejections.txt"; failed=1; fi
if grep -nE '^/' "$INBOX/members-norm.txt" >> "$INBOX/rejections.txt"; then failed=1; fi                      # absolute path
if grep -nE '(^|/)\.\.(/|$)' "$INBOX/members-norm.txt" >> "$INBOX/rejections.txt"; then failed=1; fi          # `..` traversal
dups=$(sort "$INBOX/members-norm.txt" | uniq -d); if test -n "$dups"; then printf '%s\n' "$dups" >> "$INBOX/rejections.txt"; failed=1; fi   # duplicate path
if grep -vE '^total ' "$INBOX/members-typed.txt" | grep -nE '^[^d-]' >> "$INBOX/rejections.txt"; then failed=1; fi   # symlink/hardlink/device/FIFO/socket
if grep -nE '(^|/)\._' "$INBOX/members-norm.txt" >> "$INBOX/rejections.txt"; then failed=1; fi              # AppleDouble ._* member (macOS xattr artifact)
if grep -nEv '^(happyranch\.db|(org|artifacts|kb|threads|task-attachments|jobs|dreams|work_hours|schedules|talks|skills)(/.*)?|workspaces(/[^/]+(/memory(/.*)?)?)?)$' "$INBOX/members-norm.txt" >> "$INBOX/rejections.txt"; then failed=1; fi   # unallowlisted member
if grep -nE '(^|/)(org-archive\.tar\.gz|manifest\.txt|archive\.sha256|members\.txt|members-typed\.txt|staged-manifest\.txt)$' "$INBOX/members-norm.txt" >> "$INBOX/rejections.txt"; then failed=1; fi   # smuggled transfer/inbox artifact
if test "$failed" -ne 0; then
  echo "ARCHIVE REJECTED — NOT EXTRACTING" >&2
  cat "$INBOX/rejections.txt" >&2
  exit 1
fi
echo "member screen passed — safe to extract into a clean STAGE"
```

Any rejection above **halts the runbook with a nonzero exit before extraction
ever runs**: `$STAGE` is not created, `tar -x` is never reached. Remove the
offending member at the **source**, re-run preflight (§2) and re-export
(§3–§4); never hand-fix the archive.

**5. Create a fresh, empty, founder-private staging directory.** `$STAGE` must
be created fresh for this operation — never reused, never pre-populated — and
must sit under the reserved `_pending` slug on the **same filesystem** as
`$DST`:

```bash
set -euo pipefail
STAGE="$DST_RUNTIME/orgs/_pending/$OP"
if test -e "$STAGE" || test -L "$STAGE"; then echo "STAGE ALREADY EXISTS — STOP" >&2; exit 1; fi
mkdir "$STAGE" && chmod 700 "$STAGE" || { echo "CANNOT CREATE STAGE — STOP" >&2; exit 1; }
```

**6. Extract only the accepted archive into the empty `$STAGE`.** This runs
**only** because step 4 exited zero — every rejection path already exited
nonzero and never reaches here. Run `tar` as the founder (non-root) user; the
archive bytes stay in `$INBOX` and are never copied into `$STAGE`:

```bash
set -euo pipefail
tar -xzf "$INBOX/org-archive.tar.gz" -C "$STAGE"
```

The step-4 screen ran first; step 7 re-checks the extracted tree with the **real
runtime classifier**, and steps 8–9 re-verify that nothing unsafe (symlink,
device, FIFO, escaped, unallowlisted, or malformed member) was actually
written.

**7. Run the shipped runtime classifier on the whole staged candidate.** The
step-4 screen judged **spelling and entry type only**. The authoritative
pre-publish gate is the actual runtime classifier
`runtime/portability/roots.py::classify_root_entries` — the same code the
source preflight route uses — run **offline** on the extracted `$STAGE` from a
**version-matched** HappyRanch Python environment. This is a **pre-publish
candidate check**: it is not a claim that tar inspection safely extracts
arbitrary input (step 4 already refused unsafe input), and it is not the online
preflight route, which is source-only and cannot run against the stopped
destination.

The classifier is fail-closed: every direct child of `$STAGE` must be an
allow-listed portable root with its correct type, `skills/` packages must pass
legacy-skill validation (identity/metadata/member shape), and
`workspaces/<agent>/memory` must be a real directory (a regular file or symlink
is `reject nonregular`). Any `reject` — **including an invalid skills package
or a non-directory memory entry, which the step-4 screen and a direct-child
layout check cannot see** — makes the runbook **exit nonzero** and halts
publication.

First materialize the checkout's supported environment once (if needed), then
run the gate. `HR_CHECKOUT` is declared in §1; `$STAGE` is passed as a safe,
quoted absolute path argument — never interpolated into the script:

```bash
set -euo pipefail
cd "$HR_CHECKOUT" && uv sync     # one-time: materialize the supported environment (uv; pyproject.toml + uv.lock)
(
  cd "$HR_CHECKOUT" || exit 1
  uv run python - "$STAGE" <<'PY'
import sys
from pathlib import Path
from runtime.portability.roots import classify_root_entries
stage = Path(sys.argv[1])
inventory = classify_root_entries(stage)
for e in inventory.entries:
    tag = {"include": "ok ", "exclude": "ex ", "reject": "REJ"}[e.classification]
    print(f"{tag} {e.path}" + (f"  ({e.reason})" if e.reason else ""))
if inventory.has_rejections:
    print("CLASSIFIER-REJECTED: staged candidate is NOT portable — STOP", file=sys.stderr)
    sys.exit(1)
print("CLASSIFIER-OK: every staged direct root is classifier-approved")
PY
) || { echo "CLASSIFIER GATE FAILED — NOT PUBLISHING" >&2; exit 1; }
```

Read the output: a §4-correct candidate produces **only `ok` lines**. A named
`ex` exclusion line would mean excluded bytes were archived (a §4 violation —
investigate); **any `REJ <path>  (<reason>)` line means the candidate is not
portable — stop**. A nonzero exit on any rejection **is** the gate. `uv run`
uses the checkout's locked environment (`uv.lock`, requires-python
`>=3.12,<3.15`); a `warning: VIRTUAL_ENV=… does not match the project
environment path` line on stderr (if your shell exports a stale `VIRTUAL_ENV`)
is harmless — uv uses the checkout's own locked environment. No new tool or
dependency is introduced — the classifier is already a dependency of the
shipped product (`pydantic`, `pyyaml`). Do **not** weaken this gate to a
handwritten approximation of the classifier; if the version-matched
environment genuinely cannot invoke it, stop and escalate rather than
substituting a lookalike check.

**8. Validate the extracted direct-child layout — fail closed.** This shell
check proves `$STAGE` is the **future org root itself** — not a container
holding a nested root (e.g. an `org-payload/` subdirectory) and not a dir
holding the archive, manifest, or any inbox artifact — and that every direct
child is a §4 root with its correct type: `happyranch.db` a regular file, every
other root a directory, nothing else. (The classifier in step 7 is the
authoritative content gate; this is the layout-exactness proof reused in §6.)
Any violation **exits nonzero**:

```bash
set -euo pipefail
ok=1
test -f "$STAGE/happyranch.db" || { echo "happyranch.db missing/not regular — STOP" >&2; ok=0; }
test -d "$STAGE/org" || { echo "org/ missing/not a directory — STOP" >&2; ok=0; }
for child in $(find "$STAGE" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort); do
  case "$child" in
    happyranch.db) ;;
    org|artifacts|kb|threads|task-attachments|jobs|dreams|work_hours|schedules|talks|skills|workspaces)
      test -d "$STAGE/$child" || { echo "$child not a directory — STOP" >&2; ok=0; } ;;
    *) echo "UNALLOWLISTED DIRECT CHILD: $child — STOP" >&2; ok=0 ;;
  esac
done
test "$ok" -eq 1 || exit 1
```

**9. Compare staged files to the external manifest, and validate the staged DB
and references.** Recompute the §4 manifest over the extracted tree and diff it
against the received `manifest.txt` (same tool and `LC_ALL=C` ordering as §4):

```bash
set -euo pipefail
(cd "$STAGE" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256) > "$INBOX/staged-manifest.txt"
if ! diff "$INBOX/manifest.txt" "$INBOX/staged-manifest.txt"; then
  echo "MANIFEST MISMATCH — STOP: staged tree differs from the received manifest" >&2
  exit 1
fi
echo "manifest matches"
# Linux: replace shasum -a 256 with sha256sum (same tool as §4)
```

Then run the **checked immutable staged-DB validation** from §3 step 3
(asserts `PRAGMA integrity_check` returns exactly `ok` and
`PRAGMA foreign_key_check` returns no rows — exits nonzero otherwise) against
the **published-shape** staged `happyranch.db`, with the same strict ordering:
prove no pre-existing candidate sidecars first, open with a URI
`immutable=1` (creates no `-wal`/`-shm`), then prove none were created. This
ordering is what makes the staged tree **byte-stable after validation**: the
manifest diff above ran first, the immutable checks below mutate nothing, so
no validation step can create `-wal`/`-shm` residue that would later trip the
§6 final layout gate or cause self-induced manifest drift:

```bash
set -euo pipefail
if test -e "$STAGE/happyranch.db-wal" || test -e "$STAGE/happyranch.db-shm"; then
  echo "STAGED SIDECAR PRESENT — STOP" >&2
  exit 1
fi
# GH-709 Slice A: checked immutable staged-DB validation (assert exactly
# ok / empty FK) — identical helper to §3 step 3; nonzero exit on corrupt or
# FK-invalid output makes publication unreachable.
cd "$HR_CHECKOUT" && uv run python - "$STAGE/happyranch.db" <<'PY'
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
except sqlite3.Error as exc:
    print(f"CANNOT OPEN STAGED DB: {exc}", file=sys.stderr)
    sys.exit(1)
try:
    integrity = conn.execute("PRAGMA integrity_check;").fetchall()
except sqlite3.DatabaseError as exc:
    print(f"INTEGRITY_CHECK ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
if integrity != [("ok",)]:
    print(f"INTEGRITY_CHECK NOT OK: {integrity!r}", file=sys.stderr)
    sys.exit(1)
try:
    fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
except sqlite3.DatabaseError as exc:
    print(f"FOREIGN_KEY_CHECK ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
if fk:
    print(f"FOREIGN_KEY_VIOLATIONS: {fk!r}", file=sys.stderr)
    sys.exit(1)
conn.close()
print("staged DB valid: integrity_check exactly ok, foreign_key_check empty")
PY
if test -e "$STAGE/happyranch.db-wal" || test -e "$STAGE/happyranch.db-shm"; then
  echo "VALIDATION CREATED A SIDECAR — STOP" >&2
  exit 1
fi
echo "staged DB valid; no sidecars created"
```

As the manual form of the deferred reference validation, confirm each DB-held
filesystem reference in the Step-0 consumer map (C1–C13) resolves to a staged
regular file with no symlink/escape — **with one recorded founder-approved
exception**: **C4/C5 (`jobs.stdout_path` / `jobs.stderr_path` / `jobs.cwd_hint`)**
hold terminal rows that this runbook retains **unrewritten** while not
transporting the machine-local stream bytes (§4); their stored source-absolute
paths are therefore **expected to be unresolvable after relocation**, and
historical stream links may be unavailable — this is not a gate failure, but
it must be recorded as the per-org founder-approved exception in the
operation ledger (§1.1). Treat any other missing, escaping, symlinked, or
data-shaped refusal — populated `custom_skill_versions.references_manifest` /
`assets_manifest` (C12b/C12c) or a populated `skill_lifecycle_packages` legacy
table (C13) — as a stop. If you cannot confirm a consumer resolves, escalate;
do not guess.

**10. Confirm the staged candidate is private, complete, and startable-shaped:**
it must contain a valid `org/teams.yaml` and a `happyranch.db`, and be readable
only by the founder. It remains a **candidate**, not an org, until §6.

## 6. Manual publication (founder-exclusive, no-overwrite)

Publication is a **manual, exclusive-access, check-and-publish** operation, not
a shipped atomic importer. It requires an exclusive founder-owned window with
**both daemons stopped and no other actor writing `$DST_RUNTIME/orgs/`** (no
agent sessions, jobs, or other admin — including any process on any machine
sharing that runtime).

1. **Re-check the slug is absent immediately before publish** (repeat §5 step 2).
2. **Prove `$STAGE` holds exactly the future org root — no transfer or inbox
   artifacts.** Immediately before the rename, re-run the §5 step 8 direct-child
   layout check and confirm none of the **exact operation transfer-artifact
   filenames** (`org-archive.tar.gz`, `manifest.txt`, `archive.sha256`,
   `members.txt`, `members-typed.txt`, `staged-manifest.txt`) is present
   anywhere in `$STAGE`, and that no `happyranch.db-wal`/`-shm` sidecar exists:

   ```bash
   set -euo pipefail
   # GH-709 Slice A: exact transfer-artifact gate — REJECT any match. `find`
   # exits 0 even when it prints matches, so the gate tests the captured
   # OUTPUT: a non-empty match list exits nonzero here and publication
   # (`mv`, the next fence) is unreachable. Exact operation filenames only —
   # NOT extension-wide: a legitimate evidence bundle
   # artifacts/evidence/report.tar.gz captures nothing and passes.
   hits=$(find "$STAGE" \( -name 'org-archive.tar.gz' -o -name 'manifest.txt' \
     -o -name 'archive.sha256' -o -name 'members.txt' \
     -o -name 'members-typed.txt' -o -name 'staged-manifest.txt' \) -print)
   # a failing `find` (e.g. missing $STAGE) exits nonzero here and set -e stops;
   # an empty $hits is the ONLY way past the gate
   if test -n "$hits"; then
     echo "TRANSFER ARTIFACT PRESENT IN STAGE — STOP: publication not permitted" >&2
     printf '%s\n' "$hits" >&2
     exit 1
   fi
   if test -e "$STAGE/happyranch.db-wal" || test -e "$STAGE/happyranch.db-shm"; then
     echo "STAGED SIDECAR PRESENT — STOP: no pre-existing candidate sidecars" >&2
     exit 1
   fi
   find "$STAGE" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort
   # must list only a subset of the §4 roots (happyranch.db, org, kb, …, workspaces)
   ```

   The gate matches the **six exact operation filenames** — it does **not**
   reject `*.tar.gz` extension-wide. Legitimate archived evidence bundles such
   as `artifacts/evidence/report.tar.gz` pass the classifier (§5 step 7), the
   manifest, and this final layout gate; only a member actually named
   `org-archive.tar.gz` (or one of the other five operation names) is a
   transfer/inbox artifact.

   `$STAGE` must be the future org root **exactly** — not a container holding a
   nested root (e.g. an `org-payload/` subdirectory) and not a directory holding
   the archive, manifest, or any inbox artifact. If either command prints an
   unexpected entry, **STOP** and escalate (§8).
3. **Publish with an atomic same-filesystem rename.** Because the target is
   verified absent and the window is exclusive, `mv` performs an atomic
   `rename(2)` with nothing to overwrite or merge:

   ```bash
   set -euo pipefail
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
4. **Verify publication postconditions** (both must hold):

   ```bash
   set -euo pipefail
   test -d "$DST" && test -f "$DST/org/teams.yaml" && echo "target exists"
   test ! -e "$STAGE" && echo "stage consumed"
   ```

   `$DST` must exist with `org/teams.yaml` and `happyranch.db`; `$STAGE` must be
   gone (the rename consumed it).

## 7. Mandatory pre-start zero-count gate, then start and verify

**Before any destination start**, run the **checked zero-count gate** against
the **published** `$DST/happyranch.db` — the published DB is the staged
logical snapshot renamed into place (§6), so it is self-contained and
validated with the same `immutable=1` read-only URI as §3 step 3 / §5 step 9:
prove no sidecars first, then run the gate (immutable creates no
`-wal`/`-shm`, so this gate cannot dirty the published org). The destination
may start only if the gate **asserts** every count is zero (exit 0):

```bash
set -euo pipefail
if test -e "$DST/happyranch.db-wal" || test -e "$DST/happyranch.db-shm"; then
  echo "PUBLISHED SIDECAR PRESENT — STOP" >&2
  exit 1
fi
# GH-709 Slice A: mandatory zero-count gate — ASSERT, do not print. The helper
# exits 0 only when every count is zero, and exits nonzero otherwise, so
# `scripts/daemon.sh start` (the next fence, "Then start the destination") is
# unreachable with any live work. Status literals are the current runtime
# enums (ScheduleStatus/TaskStatus/JobStatus in runtime/models.py).
cd "$HR_CHECKOUT" && uv run python - "$DST/happyranch.db" <<'PY'
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
except sqlite3.Error as exc:
    print(f"CANNOT OPEN PUBLISHED DB: {exc}", file=sys.stderr)
    sys.exit(1)
checks = {
    # runnable schedules (armed/firing) must be zero
    "schedules_armed_or_firing": (
        "SELECT COUNT(*) FROM schedules WHERE status IN ('armed','firing')"),
    # paused-vs-runnable distinction: every non-terminal schedule must be
    # explicitly 'paused' (the required suspended state until operator
    # re-arm); a schedule that is neither paused nor terminal (incl. any
    # unknown status) is a violation — no schema semantics change.
    "schedules_not_paused_nonterminal": (
        "SELECT COUNT(*) FROM schedules WHERE status NOT IN "
        "('paused','fired','cancelled','expired','failed','timeout')"),
    # live tasks must be zero
    "tasks_pending_in_progress_escalated": (
        "SELECT COUNT(*) FROM tasks WHERE status IN "
        "('pending','in_progress','escalated')"),
    # live jobs must be zero (terminal-job contract, §4)
    "jobs_pending_running": (
        "SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')"),
}
bad = []
for name, sql in checks.items():
    count = conn.execute(sql).fetchone()[0]
    print(f"{name}={count}")
    if count != 0:
        bad.append(name)
conn.close()
if bad:
    print("ZERO-COUNT GATE FAILED (nonzero: " + ", ".join(bad) +
          ") — STOP: do not start", file=sys.stderr)
    sys.exit(1)
print("zero-count gate passed: no runnable schedules, no live tasks, no live jobs")
PY
```

The `jobs_pending_running` check closes the §4 terminal-job contract: only
terminal job rows (`completed`/`failed`/`rejected`) are carried (the §2
preflight refused any pending/running job at the source), and a terminal row
can never be re-launched, so the destination must see zero launchable jobs.
The `schedules_not_paused_nonterminal` check is the paused-vs-runnable
contract: runnable schedules (`armed`/`firing`) are blocked, and every
non-terminal schedule must be explicitly `paused` — the required suspended
state until explicit operator re-arm (§7.1) — so nothing fires on the
destination before re-arm. If **any** count is nonzero (helper exit nonzero),
**stop**: do not patch the DB, do not overwrite, do not start. The export was
not taken from a quiescent source — preserve everything (§8) and redo the
export after resolving quiescence at the source (§2). The status strings come
from the current runtime code (`ScheduleStatus.armed`/`firing`/`paused`,
`TaskStatus.pending`/`in_progress`/`escalated`, `JobStatus.pending`/`running`
in `runtime/models.py`); the tables are `schedules`, `tasks`, and `jobs` with
a `status TEXT` column (`runtime/infrastructure/database.py`), and the per-org
DB file is `happyranch.db` (`runtime/orchestrator/_paths.py`).

**Batch:** run this gate for **every published org** in the batch before any
start, and record each org's counts in the operation ledger (§1.1). The
destination starts **once**, only after every requested org is either
`published` + zero-gated or explicitly `blocked` with recorded evidence and a
founder decision to proceed without it. A single start serves the whole set.

This gate is a **manual safety precondition** — it is not a shipped feature,
not a disarm command, and it performs no mutation.

**Then start the destination** using the documented daemon script and verify
normal discovery/health:

```bash
set -euo pipefail
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

**Rebinding target-local executors/adapters**, **regenerating agent
workspaces**, and **re-arming schedules** are **mandatory operator work
before any work resumes** — see §7.1. The runtime does **not** enforce
workspace regeneration or schedule re-arm and does not block startup; the
runbook makes them gating because the relocated org is otherwise not
launch-ready. Since GH-709 Slice C, the runtime **does** enforce the
per-agent readiness-marker gate inside ``init-agent`` (it never reports
``done`` without the selected profile's exact marker as a regular file —
GH-709 finding 2 is fixed), but the founder still runs and records the
§7.1 checks as the operator ledger. This manual start is allowed only
because the §7 zero-count gate passed and the source was quiescent when
exported.

### 7.1 Mandatory post-publication readiness — before any work resumes

The relocated orgs are discovered and loaded as **normal active orgs**, but the
classifier carries **no generated workspace content** — `AGENTS.md`, `CLAUDE.md`,
settings, skills links, `repos/`, `output/` are excluded by design; only
`workspaces/<agent>/memory/**` travels. **No agent can launch until the
workspace is regenerated and the executor-specific readiness marker exists.**
Until the gate below passes, do **not** dispatch tasks, resume threads, or
allow any agent invocation. The runtime enforces only the init-agent
readiness-marker gate (Slice C, step 2); the remaining checks below are
operator-enforced (honesty boundary in the header).

1. **Destination executor inventory (before any init).** For every selected
   org, list the **active AgentDef roster only** — the approved agent markdown
   files `$DST/org/agents/*.md` (exclude the `_pending/` and `_terminated/`
   subdirectories; they are enrollment/archive records, never agents to
   initialize). From each file's frontmatter, read the `executor:` profile
   name. Then verify on the **destination machine** (machine-global stores —
   never copy the source machine's registries; paths/adapters are
   machine-local):
   - **built-in profiles** `claude`, `codex`, `opencode`, `pi` are registered
     in code (`runtime/adapters` catalog) — no registration needed;
   - **custom profiles** must exist in the destination machine-global
     `executor_profiles.yaml` (`<daemon-home>/executor_profiles.yaml`, the
     runtime executor store); a referenced name that is absent there is a
     **blocked agent** — register the profile on the destination
     (`happyranch executors register|runtime-register` with an `hrreg_` token)
     before it can ever launch;
   - **binaries**: `happyranch executor-binaries list` must show every
     required built-in/custom executor kind with status `valid` (registered
     absolute path exists and is executable). Registration-only resolution
     (`runtime/orchestrator/executors.py::_resolve_binary`) never falls back
     to PATH discovery — a stale/missing registration blocks the agent with an
     actionable message;
   - record the per-org blocked-agent list (if any) in the operation ledger;
     a blocked agent must never be reported `done`.
2. **Regenerate workspaces from the active AgentDef roster only.** Run
   `happyranch init-agent --org <slug> <agent>` **per active agent by name**
   (the roster from step 1), or the bulk form `happyranch init-agent --org
   <slug>` — bulk initialization derives its targets from the canonical
   active AgentDef roster (`org/agents/*.md`) only: it never targets
   `_pending`/`_terminated` enrollments, archived definitions, team-registry
   members without an AgentDef, or stray/reserved workspace directories
   (GH-709 Slice B). Repository-backed agents will reclone/reconcile their
   configured repositories — an expected side effect of workspace
   regeneration.

   **GH-709 Slice C (shipped): init-agent verifies the readiness marker
   itself before reporting `done`.** The bootstrap materializes the
   workspace skills tree (so the claude profile marker
   `.claude/skills/start-task/SKILL.md` exists immediately after init, not
   only at first session spawn) and init-agent emits `done` **only** after
   the **selected executor profile's exact marker** exists as a valid
   regular file. An unregistered profile (not in the destination registry),
   a missing/wrong-profile marker, or a non-regular marker (directory,
   dangling link) emits a per-agent `error`, never `done`; the stream stops
   at the first error (no `all_done`) and the CLI exits nonzero. A blocked
   agent from step 1 therefore fails init rather than being reported done.
3. **Verify the exact readiness marker is a regular file (ledger
   evidence).** The runtime now enforces marker verification at init (Slice
   C), but the founder must still record the check as ledger evidence — do
   not rely on the `done` phase alone. After each agent's init, verify the
   marker for its **selected executor profile**: `claude` →
   `workspaces/<agent>/.claude/skills/start-task/SKILL.md`; `codex`,
   `opencode`, `pi` → `workspaces/<agent>/AGENTS.md`; a custom profile → its
   registered `readiness_marker_fragment` (a missing/absent profile is a
   blocked agent — init now fails it with an actionable message; step 1's
   registration check remains the gate):

   ```bash
   set -euo pipefail
   # per active agent, per its resolved profile marker — example for claude
   marker="$DST/workspaces/<agent>/.claude/skills/start-task/SKILL.md"
   if ! test -f "$marker"; then
     echo "READINESS MARKER MISSING for <agent> — STOP: agent is not launch-ready" >&2
     exit 1
   fi
   ```

4. **Zero live tasks/jobs + schedules paused.** Re-run the §7 checked
   zero-count gate on each published org (helper exit 0: no
   `schedules_armed_or_firing`, no `schedules_not_paused_nonterminal`, no live
   tasks, no live jobs) immediately before resuming work, and confirm every
   schedule is `paused` (or otherwise terminal) until **explicit operator
   re-arm** — do not rely on the relocated DB's schedule status surviving the
   move as "ready to run". Re-arm schedules one at a time only after the
   readiness gate passes.
5. **Record the ledger row** per org: `ready` (all agents bootstrapped,
   markers regular files, zero live work) or `blocked` with the blocked-agent
   list and evidence (§1.1).

This is the manual form of the deferred "exhaustive readiness report" product
feature; it is **not shipped** — nothing below verifies it, so the founder
must actually run it and keep the ledger.

### 7.2 Launch and daemon-child CLI diagnostics (noninteractive/remote shells)

The destination must be startable from the **exact shell that will run the
start** — a remote noninteractive SSH shell may not source the user profile
and can silently lose `uv`/`happyranch` from `PATH`. The shipped
`scripts/daemon.sh start` backgrounds bare `uv` and reports only a five-second
timeout (GH-709 finding 5); the synchronous in-script `uv` PATH/version
preflight is Slice D work that is **not shipped**. Until it lands, run these
operator diagnostics **before** `scripts/daemon.sh start` from that exact
environment:

```bash
set -euo pipefail
# launch diagnostics — must pass in the noninteractive launch shell
if ! command -v uv >/dev/null 2>&1; then
  echo "STOP: uv is not on PATH in the launch shell." >&2
  echo "  daemon.sh backgrounds bare 'uv'; a stripped PATH causes a silent 5s" >&2
  echo "  start timeout (GH-709 finding 5). Re-invoke with the supported" >&2
  echo "  environment (e.g. a login shell, or PATH=<explicit uv dir>:$PATH)." >&2
  exit 1
fi
uv --version
# version-matched runtime line (must match the source deployment's checkout)
(cd "$HR_CHECKOUT" && uv run python --version)   # requires-python >=3.12,<3.15
```

There is **no automatic download, no arbitrary PATH fallback, and no
alternate CLI selection**: if `uv` is missing in the launch shell, resolve the
environment (or install uv at its documented path) and re-verify — never point
the daemon at a different toolchain or a copied binary.

**Daemon-child CLI parity (after start, before any agent work) — accurately
bounded.** Agent callbacks and skills invoke bare `happyranch` inside
executor children. The daemon prepends the standard tool dirs
(`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`) to its own `PATH` at
startup when absent and passes that environment to children
(`runtime/orchestrator/executors.py::_normalize_path` / `_callee_env`); a
source/dev daemon does **not** auto-prepend its checkout's `.venv/bin`, so for
a source deployment a stable `~/.local/bin/happyranch` install/symlink is a
valid operator recovery (this is what restored the completed relocation).

**What Slice A can and cannot prove here (honest limitation).** The shipped
runtime has **no existing non-mutating daemon-child diagnostic seam**: there is
no command or route that executes a probe inside the daemon's child
environment and reports the resolved `happyranch` path, CLI version, or
checkout bound to `$HR_CHECKOUT`. Specifically, verified at the current head:

- `happyranch doctor` (`cli/commands/doctor.py`) checks the editable-install
  pointer of the **shell that invokes it** against a git-derived canonical
  source. It does **not** run inside a daemon child, and it does **not**
  compare against `$HR_CHECKOUT` — it cannot prove daemon-child
  CLI/runtime parity. Use it for what it is: operator-shell editable-pointer
  health (exit 0 = PASS, 1 = mismatch, 2 = cannot determine).
- `GET /api/v1/runtime` (`happyranch runtime`) and `GET /api/v1/health`
  (`runtime/daemon/routes/runtime.py`, `health.py`) report the daemon's
  **active runtime container root** (the org data dir) — daemon-side truth
  after start, but neither runs a child probe nor exposes the checkout path.
- `_callee_env` / `_normalize_path` are used only to **spawn** executor,
  adapter, and job subprocesses; no diagnostic subcommand uses them.

Because no such seam exists, this runbook does **not** invent an
operator-shell probe and does **not** add runtime code (Slice A is a
three-file documentation/harness change). The founder default "require the
source deployment's matching CLI/runtime environment" is therefore only
partially satisfiable here: the launch-shell `uv` diagnostics above bind the
**start** to the launch environment and `$HR_CHECKOUT`'s pinned runtime, and
`happyranch doctor` + `happyranch runtime` give operator-shell and daemon-side
health. **Daemon-child CLI/PATH/version parity bound to `$HR_CHECKOUT` is an
UNMET criterion of this runbook** — it needs a real daemon-child diagnostic
seam, which is Slice D work; record this limitation in the operation ledger
and do not claim parity. In practice: after start, run `happyranch runtime`
and `happyranch doctor` in the operator shell, record both outputs plus this
limitation in the ledger, and keep schedules paused (§7) until Slice D's
seam ships.

## 8. Failure handling and escalation

- **Before publication (staging/validation):** retain the source intact and
  clean **only** the exact operation directories you created — the staging
  `_pending/$OP` and the receive inbox `_pending/$OP.inbox` — after inspection,
  with both daemons stopped. Never `rm -rf` broadly, never touch `_pending`
  beyond your own operation directories. Re-run from §2 after fixing the cause.
- **Runbook-created source sidecar residue (§3 step 2):** if the read-only
  backup created a `happyranch.db-wal`/`-shm` beside the source (a verified
  property of a WAL-mode read-only open), record it in the operation ledger
  and leave it in place. Slice A **never deletes source sidecars** — removing
  the residue is a separate founder-authorized **destructive-cleanup**
  decision, never performed by this runbook (pre-existing sidecars block the
  runbook at §3 step 1 and also stay untouched).
- **Batch: a failing org never rolls back a published org, and no successful
  org masks a failure (§1.1).** Each org's validation/publish decision is
  independent. A candidate that fails validation stays staged (or is cleaned
  per the bullet above) and is recorded `blocked` in the operation ledger with
  its evidence; orgs that already published are **not** reverted, and their
  source copies and evidence remain untouched. The destination starts only
  after every requested org is `published` + zero-gated or explicitly
  `blocked` with a founder decision (§7) — a sibling's success never hides a
  failure from the ledger.
- **Terminal-job exception recording.** Any per-org founder-approved exception
  (notably the C4/C5 terminal historical job path disposition, §4/§5 step 9)
  must be recorded **per org** in the operation ledger — never carried silently
  as a batch-wide exception.
- **After publication but before start** (a nonzero §7 count or a failed §6
  postcondition): do **not** overwrite, re-run, or merge the published tree, and
  do **not** hand-edit the DB. Leave the destination **stopped**, preserve the
  source intact, record the evidence (the §7 counts, `scripts/daemon.sh status`,
  the §6 postcondition output), and escalate.
- **Post-start / readiness failure** (a §7.1 blocked agent, a missing
  readiness marker, a failed §7.2 diagnostic): do **not** resume work. Leave
  the affected agent(s) unlaunchable, keep schedules paused, record the
  blocked list in the ledger, and resolve through the documented surfaces
  (`happyranch executor-binaries register`, executor profile registration,
  `happyranch init-agent`, the `PYTHONPATH=` doctor remedy) before re-running
  the readiness gate.
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
- Daemon lifecycle: `scripts/daemon.sh` (`start` / `stop [--force]` / `status`;
  note the bare-`uv` background at `start`, §7.2).
- Runtime layout + reserved slugs: `runtime/runtime.py`
  (`RuntimeDir.iter_org_roots`, `_RESERVED_ORG_SLUGS`, `_SLUG_RE`) — the
  loader-backed inventory recipe in §1.1.
- Per-org DB filename: `runtime/orchestrator/_paths.py` (`db_path` →
  `happyranch.db`).
- Status enums + tables: `runtime/models.py` (`TaskStatus`, `ScheduleStatus`,
  `JobStatus`); `runtime/infrastructure/database.py` (`tasks`, `schedules`,
  `jobs`).
- DB-to-filesystem reference map:
  `docs/superpowers/specs/org-portability-reference-consumers.md`.
- Job stream reads use the stored absolute `stdout_path`/`stderr_path`
  (`runtime/daemon/routes/jobs.py` tail/read) — the reason historical stream
  links may be unavailable after relocation (§4). Only `pending` rows launch;
  terminal rows are never re-launched (§7).
- Executor registration/readiness: `runtime/orchestrator/executor_registry.py`
  (`ExecutorRegistry.get_profile`, `readiness_marker_fragment`),
  `runtime/adapters/__init__.py` (`_BUILTIN_CATALOG` markers),
  `runtime/orchestrator/runtime_executor_store.py`
  (`<daemon-home>/executor_profiles.yaml`),
  `runtime/orchestrator/executor_binary_registry.py`
  (`<daemon-home>/executors.json`), `cli/commands/executor_binaries.py`
  (`happyranch executor-binaries list`), `cli/commands/executors.py`.
- Daemon-child PATH: `runtime/orchestrator/executors.py` (`_normalize_path`,
  `_callee_env`, `_resolve_binary`) — registration-only binary resolution, no
  PATH fallback (§7.2). No daemon-child diagnostic seam exists (see §7.2
  limitation).
- CLI editable-install health (operator-shell only, never a daemon child):
  `cli/commands/doctor.py` (`happyranch doctor`); daemon-side active runtime
  root: `runtime/daemon/routes/runtime.py` (`GET /api/v1/runtime`) and
  `runtime/daemon/routes/health.py` (`GET /api/v1/health`) (§7.2).
- Workspace regeneration + readiness marker: `runtime/daemon/routes/agents.py`
  (`init_agents`, `ContextBuilder.ensure_workspace_ready`, and the Slice-C
  `_bootstrap_readiness_marker` helper that materializes the skills tree and
  verifies the selected profile's exact marker is a regular file before
  `done`), `cli/commands/agents.py` (`happyranch init-agent`, exits nonzero
  when the stream ends without `all_done`); the runbook drives it per active
  AgentDef and records the §7.1 check as ledger evidence.
