# Current THR307 nightly disk sweeper contract

Authority: Founder THR307 seq51 approves seq48, engineering seq49 and consultant
seq50; sole delivery root TASK-11518. This new mechanism replaces the unimplemented
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
  both owner and root exactly completed/failed/cancelled. Missing, conflicting,
  superseded, escalated, active or unknown facts retain. Revisit names are not
  parentage. Recorded `session_start.workspace` must consistently match the
  literal workspace. A task-like directory name alone proves no ownership.
* Complete native jobs-table inspection, no pending/queued/running job anywhere
  in that structural root's task scope. Unknown job ownership/lineage/status
  fails closed; missing evidence is not an empty job set. Applies to both phases.
* Fresh Linux `/proc` inspection of all visible **same-effective-UID** process
  leaders and every thread, with bracketed identity and thread/PID census. It
  reads cwd/root/exe, all fd links and TMPDIR/TMP/TEMP; temp aliases resolve
  through the inspected task's root. Different mount namespaces refuse rather
  than inventing host-path equivalence. There are no legacy daemon exemptions.
  Denial, malformed credentials, missing live-task references, unstable census
  or incomplete evidence retain unknown. Confirmed process/task disappearance
  differs from denial; stable zombies have no live references. Other UIDs are
  outside this scope; this is **not host-wide absence**, a writer fence or
  protection from a later opener/hostile same-UID rename.
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
primary-root ownership, ordinary no-follow primary `.git`, unlocked/nonprunable
registration, correct owning task branch or detached HEAD, exact common-dir and
toplevel, clean porcelain status including every nonignored untracked file, and
empty `git rev-list -n1 HEAD --not --branches --remotes`. These Git facts are
re-read after slow common inspections. Dirty paths are reported. Unique detached
commits remain. Local branches/remotes preserve committed work without GitHub
publication; Git-ignored contents inside an admitted tree are disposable.
“Re-creatable” refers to committed contents only.

Use `git worktree remove` without `--force`; keep branches. After ordinary failure
there is at most one contained permission repair and retry, each with full fresh
eligibility. This Git version can unregister a worktree on permission failure;
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

Run `.venv/bin/python scripts/verify_disk_sweeper.py` after `uv sync --frozen`.
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
and separate discovery exhaustion. Actual mount/chroot isolation availability
and any missing cases must be reported, not laundered into PASS. No protected
mutation proof or historical/real cleanup runs here. General integration remains
SKIPPED24342, Python units RETIRED, Product E2E daily-only. Independent full code
review and independently executed scoped QA remain required after publication.
