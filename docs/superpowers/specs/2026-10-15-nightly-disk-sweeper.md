# Current THR307 nightly disk sweeper contract

Authority: Founder THR307 seq51 approves seq48, engineering seq49 and consultant
seq50; original delivery root TASK-11518. Founder seq85/88 authorizes the
three bounded corrections under continuation root TASK-11742; TASK-11518 remains
cancelled. This new mechanism replaces the unimplemented
capacity/rotation proposal, not any legacy cleanup implementation or its rules.
The operator disk/output reports are inputs, not independent feature evidence.
Retry counts in those reports are not distinct removed-root counts. No operator
reclamation is attributed to this feature.

## Schedule, configuration and lifecycle

`runtime/daemon/disk_sweeper.py` registers through `app._lifespan`. Once at the
next future **03:00 daemon-machine local time**, orgs run sequentially in lexical
root order. The OS resolves the occurrence's local UTC offset; org `timezone`
does not change this schedule. Effective zone name/offset and scheduled date are
recorded. Startup never catches up a missed occurrence. `last-scheduled.json` is
reserved before launch; restart or interruption does not retry that date. Corrupt
metadata refuses execution. This file contains no candidate cursor or rotation.

One **1800-second monotonic deadline for the entire batch** includes source
identification, org/candidate discovery, all scans, Git and both deletion phases.
It is never reset per org, candidate or retry. Candidates are phase1 then phase2,
oldest root mtime first within each phase, with literal absolute path as stable
tie-break. This ordering is not an age admission test. There is no count cap or
saved position. Discovery itself can exhaust the budget; unfinished discovery,
unreached scope and interrupted mutation remain unknown/incomplete. Refusal cost
is not promised. Many refusals before an eligible root are a finite disposable
acceptance case, not a general fairness guarantee.

Discovery retains the known candidate set when an independent workspace,
task-temp subtree, repository or registered path cannot be read. Each failed
scope is preserved and named literally in `discovery_scopes` (also under
`unknown_scope`), with its actual reason and `candidate_count: null`. It does
not become an empty inventory. Only an immediate repository with a no-follow
primary `.git` directory is queried; arbitrary children such as `repos/output`
and `repos/.happyranch` are reported uncertain without walking their contents.
Workspace/repository/metadata aliases are not followed. Before trusting Git's
list, a complete no-follow linked-admin census reconciles each registration
and its reciprocal worktree pointer; Git exit0 can silently omit unreadable or
missing records. A failed census retains the entire repository scope, including
otherwise readable worktrees. Missing optional `task-tmp`/`repos` directories
are absent; inaccessible or invalid ones are unknown.

Independent known scopes continue within the SAME deadline. Completed traversal
with any uncertain scope is `discovery: partial`; org and batch outcomes remain
incomplete. Known candidates retain phase/oldest/path ordering and pass all
current action-time gates. Partial discovery never permits Git prune. On
expiry/shutdown, discovery stops globally: captured candidates remain explicitly
unreached, discovery is unknown, and `unfinished_discovery_root` names the extent
whose remaining inventory is unknown. `known_candidate_count` and
`known_unreached_count` describe only captured candidates; `unreached` remains
null unless discovery is complete. Other candidate/retention/allocation counts
likewise describe the known set, never an uncertain total. Discovery checkpoints
retain captured candidates and failed scopes without a cursor or budget reset.
The existing supervisor conservatively retains `partial_mutation: unknown` for
a nonzero worker exit, even when an incomplete report was fully written; this
field is not evidence of deletion.

```yaml
# <runtime>/orgs/<slug>/org/config.yaml
# Quote on/off: YAML 1.1 parses unquoted tokens as booleans, which are rejected.
disk_sweep:
  mode: "dry_run"  # "off" | "dry_run" | "on"; absent defaults dry_run
```

Only `mode` is accepted in this block; invalid/missing evidence fails safely.
Off performs no candidate inventory or cleanup. Dry run performs no chmod,
unlink/rmdir, Git remove or prune. Mode is re-read before each on-mode mutation
and retry; disabling prevents later admissions, not revocation of an admitted
syscall. Summaries/audits are permitted reporting writes.

Blocking work belongs to a separate, owned process group. Git inherits the group
and has a maximum15s lifetime clipped to remaining budget. The parent kills the
whole group at deadline/shutdown and waits for its worker, including when the
leader exited before a child. App shutdown awaits this stop before closing org
DBs. It does not cancel a `to_thread` deletion and leave its worker running.
Summary/source identification and audit work may use threads; they never delete
candidates. A process stuck in uninterruptible kernel I/O may delay termination
or reaping: this is not a real-time kernel guarantee. Interruption records unknown
partial mutation rather than success; the prior checkpoint identifies in-flight
scope. No admission after deadline or extra deletion grace is granted.

## Common eligibility and observation scope

Every admission, including permission repair and the one Git retry, requires:

* Existing authoritative task record assigned to the actual workspace owner;
  a complete acyclic `parent_task_id` walk to a real NULL-parent structural root;
  both owner and root exactly completed/failed/cancelled/superseded. Missing,
  conflicting, escalated, active or unknown facts retain. Revisit names are not
  parentage. Recorded `session_start.workspace` must consistently match the
  literal workspace. A task-like directory name alone proves no ownership.
* Complete native jobs-table inspection, no pending/queued/running job anywhere
  in that structural root's task scope. Recognized finished jobs exactly
  completed/failed/rejected are skipped BEFORE owner-lineage lookup, including
  orphaned historical owners. Unknown status retains; every active job must have
  complete structural lineage. An active job in the candidate's structural root
  retains; known unrelated active roots do not block. Missing evidence is not an
  empty job set. Applies to both phases and refreshed Git repair/retry gates;
  stored rows are never repaired or archived.
* Fresh Linux `/proc` inspection of all visible **same-effective-UID** process
  leaders and every thread, with bracketed identity and thread/PID census. It
  reads cwd/root/exe, all fd links and TMPDIR/TMP/TEMP; temp aliases resolve
  through the inspected task's root. A readable different mount namespace
  refuses rather than inventing host-path equivalence. On PermissionError only,
  the exact stat comm names `systemd`, `(sd-pam)`, `sshd-session`, `ssh-agent`
  may omit that denied reference. All remaining readable references are still
  inspected, including after a denial; readable candidate use always retains.
  This includes denied namespace/fd-directory/environment/temp-alias reads,
  never denied identity or credential evidence. Exact PID/starttime/comm and
  same-UID checks bracket the exception; names shorter than Linux's comm limit
  avoid truncated-prefix matching. No argv substring, generic SSH or Codex
  allowance exists. Unknown identity/credentials, other unreadable names,
  missing live references, namespace mismatches and unstable census still retain.
  Applied exemptions are checkpointed as candidate `process_exemptions` and
  aggregated under the same org key, with PID/TID/starttime/name/UID/reason and
  candidate path, including if later evidence refuses. Repeated observations of
  the same identity within a candidate are deduplicated; this is not a global
  process count. Existing JSON and org audit payloads carry these records.
  Confirmed disappearance and stable zombies retain their separate handling.
  Names are not executable authentication. Other UIDs are outside this scope;
  this is **not host-wide absence**, a writer fence or protection from a later
  opener/hostile same-UID rename.
* A complete no-follow descendant scan, all entries at least3600s old by BOTH
  mtime and ctime; atime is excluded because observations may update it. Root
  mtime alone never admits. Unreadable directories, special objects, external
  hardlinks, identity drift and filesystem/mount boundaries retain. Exact own
  chmod mode/ctime changes may be recognized on Git retry, with all other gates
  refreshed; unknown changes never receive that exception.

Paths open from `/` one component at a time with O_NOFOLLOW. Tree syscalls use
verified directory fds and recorded inode/device identities. Mountinfo rejects
same-device bind mounts as well as device changes. Internal symlinks are unlinked
only, never traversed or chmodded; root/ancestor symlinks refuse. Permission repair
adds owner-write on admitted directories through verified fds, never `chmod -R`.
Tree mutations recheck identities and deadline before each syscall. POSIX unlink
is still pathname-based; a hostile same-UID replacement in the final check/syscall
window is not fenced. External hardlinks retain to avoid changing unknown/durable
inodes. Internal hardlinks track own unlink nlink/ctime effects and count once.

## Phase1: task temporary roots

Only `<workspace>/.happyranch/task-tmp/TASK-*` is discoverable. The current
`task_scratch.prepare_task_scratch` producer creates disposable roots and sibling
`.happyranch/task-scratch-manifests/<task>.json` plus `.lock` as **durable recovery
artifacts**. The existing strict bounded manifest validator supplies additional
classification/root evidence; it is never sole deletion authority. Missing or
invalid manifest retains. Sibling manifests/locks are not candidates or repaired.
Scratch containing `.git` evidence is retained unknown. `output/`, caches,
`.happyranch/scratch` and any other target are outside candidate discovery.

An admitted scratch operation repairs its contained directory permissions and
uses fd-relative unlink/rmdir for that exact root. It never invokes generic rm.
Failure is recorded partial/unknown and is never retried with more force.

## Phase2: registered task worktrees

Only actual Git registrations exactly under an owner's
`repos/*/.claude/worktrees/TASK-*` qualify. Require matching guard task/worktree/
primary-root ownership, ordinary no-follow primary `.git`, and a complete
`.git/worktrees` inventory reconciled with Git output and literal task-worktree
roots. The linked-admin directory, each entry and its files must be readable,
ordinary and unaliased, with matching `gitdir`, `commondir`, `HEAD` and worktree
`.git` backlinks. Missing, malformed, redirected, conflicting or unexpected
metadata is unknown; no worktree from that uncertain repository is salvaged.
A genuinely absent linked-admin directory with primary-only Git inventory and
no surviving task-worktree roots is a valid empty control. Conservative pointer
validation accepts canonical absolute backlinks and ordinary `../..` or exact
absolute common-dir pointers; other spellings retain unknown. Also require an
unlocked/nonprunable registration, correct owning task branch or detached HEAD,
exact common-dir and toplevel, clean porcelain status including every nonignored untracked file, and
empty `git rev-list -n1 HEAD --not --branches --remotes`. These Git facts are
re-read after slow common inspections. Dirty paths are reported. Unique detached
commits remain. Local branches/remotes preserve committed work without GitHub
publication; Git-ignored contents inside an admitted tree are disposable.
“Re-creatable” refers to committed contents only.

Use `git worktree remove` without `--force`; keep branches. After ordinary failure
there is at most one contained permission repair and retry, each with full fresh
eligibility. Registration and containment identities are refreshed immediately
before removal, repair, retry and prune, including after the first Git failure.
A metadata transition marks the repository scope unknown with null total counts,
retains its remaining worktrees and prohibits prune; earlier partial effects and
verified completed removals retain their actual accounting. This Git version
can unregister a worktree on permission failure;
that partial/unregistered root fails fresh admission and is retained, with no
re-registration, generic rm or stronger retry. Timeout aborts the entire worker,
so potentially surviving descendants are stopped by its supervisor.

Git prune has no exact-entry option. The implementation allows only a proven
empty linked-worktree admin scope (primary-only registration, no admin entries).
Any linked/unrelated/prunable/unknown registration retains the entire prune scope.
Normal successful removal already removes its own registration. Prune is on-mode
only, not a missing-directory reclamation claim. No unrelated record is blindly
pruned, and missing/prunable directories contribute no removed bytes.

## Reports and operational acceptance

One JSON under `<daemon_home>/disk-sweep/<scheduled-date>.json`, and one existing-
shaped `disk_sweep_summary` audit per swept org (no table/column change). Manual
runs use a unique manual filename and one permitted audit per applicable org.
Audits keep existing empty task scope/daemon attribution. No per-candidate receipts
or new ledger. Summary includes actual executing source hash, config digest/mode,
zone/window/date, discovery status, outcome, retained reasons/paths, dirty paths,
known eligible leftovers, unreached counts/orgs and errors. In-flight checkpoint
survives ordinary worker interruption; report failure is never fabricated success.

`measured_allocated_bytes` counts eligible observed `st_blocks*512` once per inode
across the whole batch. Retained/unreached allocation is explicitly unknown.
`removed_attributable_bytes` counts only verified successful candidate removal,
with inode deduplication; retries are not separate reclamation. Partial mutation
sets attribution_unknown, preserves the known lower bound, and claims no invented
bytes for that attempt. Physical freed bytes remain null (links, open files,
shared extents and unrelated changes prevent that inference). Whole-disk free
percentage is context only. Below20% is flagged in the existing audit/JSON;
there is no suitable standalone founder disk-alert interface to call, so no new
notification routing/subsystem is introduced.

Supported manual **dry run only** (no automatic fallback/activation):

```bash
python -m runtime.daemon.disk_sweeper --dry-run --runtime-root /absolute/runtime
```

`task_scratch_reclamation.py`, `workspace_cleanup.reclamation_actions_enabled`,
legacy cleanup procedures and seven nightly agent cleanup sessions are unchanged.
No production cleanup, schema/auth/permission change, deployment, restart or
activation follows from this source delivery. Manager owns reviewed merge,
exact-merge static checks and approved dry-run deployment/restart, serving-source/
config verification and the first **scheduled** dry-run. Founder decides on-mode
activation explicitly. Seven actual on-night records of attributable results,
retained/unknown/eligible backlog and errors precede separately approved cutover.
~50GiB is provisional; output growth or operator deletions cannot determine PASS.

## Finite disposable verification

Historical broader driver (not authorized for the seq85 three-case delivery):
`.venv/bin/python scripts/verify_disk_sweeper.py` after `uv sync --frozen`.
This standalone script imports no retired unit selection and uses real files,
Git and processes with synthetic task/job/audit projections. An advanced wall
clock makes fresh fixture ctimes old; monotonic budgets remain real. It covers
the approved cases1–14: eligible root with durable controls/hardlink accounting;
invalid lineage/status; actual cwd/exe/fd and root inspection; all three process
temp variables; readonly scratch and bounded Git recovery; symlink/ancestor
containment; dirty/untracked/ignored worktrees; fully nonmutating dry run and
public CLI; missing/denied evidence; all active job states and retry change;
nested recent writes; partial failure/deadline/shutdown/event-loop responsiveness;
unique detached versus retained refs; fifty refusals before an eligible candidate
and separate discovery exhaustion. Focused correction cases15–18 additionally
cover the real first-night non-Git sibling failure, valid scratch and registered
worktrees before/after failed scopes, real unreadable/broken registrations,
workspace/repo/metadata aliases and non-directory children, nonmutating dry-run,
independent org continuation and JSON/audit projection, a real Git timeout during
discovery with captured/unreached/unknown inventory, and finite progress after
35 uncertain subtrees plus30 refused candidates. Cases19–22 cover real mode000
linked-admin/entry/gitdir and missing admin/entry/pointers, Git success with omitted
records, valid absolute-commondir aliases, entry/pointer aliases and conflicting
backlinks. They assert no Git consumption/mutation of uncertain metadata, exact
restored bytes/modes/registrations and branch preservation, dry-run nonmutation,
and independent on-mode scratch/repository progress. Task-local orchestration
changes real metadata after discovery, first actual Git failure, owned repair
and completed removal before prune. Actual permission-failure unregistration
remains partial/unknown; no repair/retry or invented bytes follow. Primary-only,
locked and metadata-enumeration-deadline controls complete the focused cases:

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --cases 15 16 17 18 19 20 21 22
```

All disposable mutation roots must be inside that actual child's task-tmp.
Actual mount/chroot isolation availability
and any missing cases must be reported, not laundered into PASS. No protected
mutation proof or historical/real cleanup runs here. General integration remains
SKIPPED24342, Python units RETIRED, Product E2E daily-only. Independent full code
review and independently executed scoped QA remain required after publication.


### Focused recovery fixtures and isolated boundaries

Historical QA TASK11530 terminal-job lineage refusals are superseded by
Founder seq85. The maintained case9/10 lineage refusal helpers now cover active
jobs only; their other unknown-status, active scope and retry checks remain.
These broad historical cases are NOT authorized for this correction delivery.

Current bounded execution is exactly three cases, with paired assertions inside
one case per correction:

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --corrections-only
```

C1 exercises both shipping dry-run phases with finished orphan jobs unchanged,
active orphan/unknown status refusal and a valid active job in the structural
root. C2 exercises superseded owner AND root eligibility with actual allocation
and unchanged disposable files/Git registrations, paired active/escalated/unknown
owner/root refusals. C3 exercises the real process scanner against a synthetic
proc syscall reader, all four exact denied identities, actual checkpoint JSON
and audit payload projection, nonlisted/near-name denial, readable use (also
after a denial) and changed-start refusal. Fixtures reuse the disposable builder
and forbid mutation during these dry runs. Synthetic SQLite/proc inputs and the
advanced wall clock are explicit; filesystem/Git gates and outcomes are real.
No actual host permission denial or broad operational acceptance is established.
Missing baseline/defect/repetition proof stays UNEXECUTED, not test-authoring PASS.
The selector skips config checks and all historical/isolation cases. General
integration SKIPPED24342; seq62 mount/process-root fixtures SKIPPED/UNEXECUTED.

The following existing isolation source describes separate historical coverage;
it is not execution authority for this three-case delivery.

The driver now invokes actual executable boundary fixtures, not `unshare true`:

```bash
# Run as the ordinary unprivileged user, from this exact source worktree.
# Each command has a 40s child lifetime and returns 77 if isolation is unavailable.
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_disk_sweeper.py --isolation-case mount
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_disk_sweeper.py --isolation-case root
# Also part of --cases 3 6 and the full driver; unavailable => PARTIAL, exit77.
```

Only `scripts/_verify_disk_sweeper_isolation.py` runs as namespace-mapped UID0,
inside a newly created unprivileged user/mount/PID namespace with private
propagation and private procfs. The whole driver refuses UID0. The venue needs
Linux util-linux `unshare`, unprivileged single-user uid/gid mapping, permission
to create mount/PID namespaces and mount private procfs/tmpfs/binds (CAP_SYS_ADMIN
in that new user namespace), and chroot there (CAP_SYS_CHROOT in that namespace).
It requires neither host root nor host capability changes. Never use sudo,
provision privileges, alter host mount propagation, mount existing data, or switch
executors/jobs after a sandbox refusal. Manager owns separately reviewed venue
resolution. Capability refusal is UNAVAILABLE; assertion errors/timeouts are FAIL.

Mount fixtures create only fresh disposable source/target paths. A nested tmpfs
bind proves a different st_dev; an ordinary bind proves identical st_dev plus a
real mountinfo boundary. Shipping on-sweeps must retain both phases specifically
with filesystem_boundary, zero mutation/attribution, identical external sentinel
bytes/modes and Git registrations. Late same-inode nested self-binds enter the
actual shipping contained mutator after final scratch admission and after a real
Git failure plus refreshed repair admission, proving the mutation-time mountinfo
refusal too. That interrupted attempt remains partial/unknown with no invented
bytes. Mount IDs are recorded at creation and checked before exact reverse-order
nonforce unmount. Cleanup never walks a still-mounted bind.

Root fixtures launch a live same-effective-UID child, chroot it into each candidate
and deliberately leave cwd outside; exe, all fds and temp environment are checked
outside too. The shipping process reader must actually read that child's root
and return exactly process_use; unknown/unreadable refusal fails the assertion.
The child is killed/reaped before fixture cleanup. Namespace PID1 and the outer
bounded process-group wrapper constrain child lifetime, including timeout. No
host-wide process absence or race-proof ownership is claimed.

These fixtures are authored executable sources; their availability and actual
results must accompany each handoff. QA11530's EPERM mount/chroot attempts and
lineage defect, and TASK11525's alias defect, remain historical evidence. No
source approval/static success replaces fresh independent behavioral QA, and
the two positive mount/root fixtures remain SKIPPED/UNEXECUTED under Founder
THR307 seq62, never PASS. They are excluded from correction verification; do not
execute them through cases3/6, the full driver or isolation flags on this waiver.


## C3 online-move composition (TASK11345 / Founder296seq135)

The scheduled daemon disk worker reserves a global native writer per admitted
org before spawn, through supervision/reap/report. Held/unowned orgs defer with
bounded codes in the existing summary/audit; other admitted orgs continue. On-mode
child admission and prune re-read the actual parent's active global writer ID
through strict append-only history and validate the optional move layout/hold.
An installed layout with missing/unknown ownership refuses before mutation; absent
layout preserves native behavior. Missing/incomplete effect closure, interruption
and partial mutation retain unknown writer evidence; a clean later provider result
cannot erase it. The common1800-second window includes reservation admission.
Off/default dry_run, metadata/process/age/containment safeguards and manual dry-run
remain; no cleanup/activation authority follows. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. APPLY_AVAILABLE=false, no COMPLETE writer; D/E/F remain root-owned.
