# Current THR307 nightly disk sweeper contract

Authority: Founder THR307 seq51 approves seq48, engineering seq49 and consultant
seq50; original delivery root TASK-11518. Founder seq85/88 authorizes the
three bounded corrections under continuation root TASK-11742; TASK-11518 remains
cancelled. Founder seq117 removes OS-process eligibility under the SAME
continuation root TASK-11742, superseding seq85/88 four-name and PR1110 zombie
handling for this independent sweeper only. Founder seq134/136 directs scheduled
deletion for every admitted org without per-org configuration or another
activation decision after delivery. Failed TASK12280 remains historical;
ROOT TASK12353 owns this same-PR recovery and normal rollout. Engineering
seq141/151 committed the daemon-wide stop described below within Founder134; original TASK11742/JOB4898 recovery12279 and orphan-process root11948/
PR1128 keep their separate scope and evidence. This mechanism replaces the unimplemented
capacity/rotation proposal, not any legacy cleanup implementation or its rules.
The operator disk/output reports are inputs, not independent feature evidence.
Retry counts in those reports are not distinct removed-root counts. No operator
reclamation is attributed to this feature.

## Schedule, configuration and lifecycle

One daemon-wide operator control is `<daemon_home>/disk-sweep/stop.json`.
Its only supported content is a JSON object containing exactly `stop` with a
real Boolean: `{"stop":true}` suspends scheduled deletion for all orgs;
`{"stop":false}` or a genuinely absent literal file permits the default policy.
The control is read afresh once per regular scheduled attempt, after the normal
same-night reservation and before native writer reservation or worker launch,
inside the shared1800s budget. Invalid/unreadable/oversized/truncated/changed
content, wrong/extra/duplicate keys or types, invalid UTF8, symlinks (including
dangling links or aliased parents), hardlinks, directories and special files
visibly suspend. Only final-file ENOENT means absent; other I/O failures suspend.
Malformed control does not prevent daemon/org startup. Operators alone write or
remove this local file; there is no per-org override, setting/API or new schedule.
Suspension logs its cause and, when publication is safe, writes `outcome:
suspended` with daemon-scoped `scheduled_admission` (`path,state,reason` and an
error when invalid) in the existing summary/audit payload. All orgs remain
unreached, with no worker/PID, reaping, removal-byte or successful-sweep claim.
The existing audit empty-task/daemon envelope and org ordering remain. Marker
and report reads/writes traverse literal no-follow parents; publication uses a
new exclusive pending inode and descriptor-relative replacement. A missing or
aliased parent, aliased/hardlinked/special final metadata or existing pending
entry refuses without writing through that path. Scheduled work never creates
a missing control parent. Refusal is visible through the existing daemon log;
a summary or org audit may be unavailable and must not be claimed as persisted.
Only manual preview can create missing report parents, with no-follow traversal.
After the single stop observation, the actual initial date summary is published
before any native writer reservation. Allowed admission initially records
`incomplete`, empty org results and all orgs unreached, without a finished/PID or
mutation claim. Unsafe date-summary or pending metadata therefore refuses with
zero reservations or worker effects. Later checkpoint/publication failures after
genuine reservation retain native uncertainty; this initial observation is not
a fence against subsequent filesystem changes.
These are filesystem observations, not a racing-rename fence or hard real-time
certification. Publication failure never permits deletion. Manual `--dry-run` remains usable and nonmutating with
true, invalid or absent control; it may write its existing reports/audits.
Removing/fixing the file affects the NEXT existing scheduled attempt only:
no same-night catchup/retry or marker reset. This is a run-admission boundary;
writing stop does not cancel an already launched worker or promise a hard
real-time stop. Existing deletion/ownership/age/containment gates still apply.

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
and `repos/.happyranch` may be listed as ordinary non-clone exclusions only after
positive classification (below), without walking their contents.
Workspace/repository/metadata aliases are not followed. Before trusting Git's
list, a complete no-follow linked-admin census reconciles each registration
and its reciprocal worktree pointer; Git exit0 can silently omit unreadable or
missing records. Founder THR307seq235 approving234 isolates ordinary local file
errors only after complete literal entry/name observation, fresh common/admin/entry
identities, canonical unique reciprocal pointers and literal task-root reconciliation
prove attribution and containment. An unreadable ordinary index, missing HEAD or
changed/disappeared auxiliary file retains that proven registration with the exact
metadata path and native cause; independently validated same-repository peers can
progress. Under Founder THR307seq285–290, a safe literal admin entry with final
`gitdir` ENOENT may instead remain an unmapped residue after exhaustive census,
stable identities and conventional-root reconciliation. An empty entry qualifies;
a nonempty entry requires valid commondir and strict checks of every remaining
object. A literal unregistered conventional TASK directory without a `.git`
pointer instead retains only that path with
`unknown_unregistered_task_worktree:<exact path>`, latched for the attempt.
Additive TASK roots and new canonical reciprocal TASK-folder/admin-entry pairs
across either census comparison or Git listing retain only the arriving task with
that same reason; a new pair's admin name must also be a TASK name. Existing
identities and pointers remain exact. Missing/changed existing entries or mappings,
non-TASK additions, ancestor loss, non-ENOENT, unsafe aliases/hardlinks/special
objects, conflicting backlinks and unknown descendant locations still refuse
the shared scope. These local paths protect containing trees at every final
remove/repair/retry boundary and make discovery partial. Coverage below distinguishes
located keeps and proven arrivals from unknown discovery. No prune runs.
The unmapped admin residue
scope is its literal admin path with candidate_count=0 and the exact native
`unknown_git_metadata:<gitdir>: <ENOENT>` cause. No W, candidate, HEAD, branch,
linked row or allocation is inferred; zero counts known candidates only, leaving
location/allocation unknown. Discovery remains partial and outcome incomplete.
Residue and local errors latch for the attempt; healing never grants clearance.
No residue repair, metadata cleanup or repository-wide prune is allowed. Git rows
must still equal the primary plus independently proven linked paths, except
captured-list omissions of newly arriving validated TASK pairs, which remain
locally retained. Only actual unmapped admin residue omissions are otherwise
explained. Other discrepancies refuse shared scope.
Missing optional
`task-tmp`/`repos` directories
are absent; inaccessible or invalid ones are unknown.

Independent known scopes continue within the SAME deadline. Completed traversal
with any uncertain scope is `discovery: partial`; unknown discovery keeps org
and batch outcomes incomplete. Located keeps and proven listed arrivals can satisfy coverage. Known candidates retain phase/oldest/path ordering and pass all
current action-time gates. Partial discovery never permits Git prune. On
expiry/shutdown, discovery stops globally: captured candidates remain explicitly
unreached, discovery is unknown, and `unfinished_discovery_root` names the extent
whose remaining inventory is unknown. `known_candidate_count` and
`known_unreached_count` describe only captured candidates; `unreached` remains
null unless discovery is complete. Other candidate/retention/allocation counts
likewise describe the known set, never an uncertain total. Discovery checkpoints
retain captured candidates and failed scopes without a cursor or budget reset.
The supervisor retains `partial_mutation: unknown` for an interrupted worker
(deadline, shutdown, or a nonzero exit other than the orderly incomplete exit 3);
this field is not evidence of deletion.
The worker exits 0 for a complete sweep and 3 (`WORKER_INCOMPLETE_EXIT`) after
publishing its final report with any other outcome; the summary records
`worker_returncode`. Exit 3 is not an interruption. Any other nonzero exit,
deadline or shutdown still records `interruption` and `partial_mutation: unknown`.
Each org's `disk_sweep` native writer settles independently: only when its own
sweep provably returned (a later org entry exists, or the worker was not
interrupted), ran in `on` mode, has no `error`, and no candidate is
`inspecting`, `mutation_in_flight` or `partial_or_unknown`. Partial discovery and
retained candidates are known effects and settle. One org's failed, incomplete or
interrupted sweep never leaves a finished peer's writer unknown (previously every
incomplete nightly run left an `unknown` writer in every org, which startup
holds on).

Scheduled deletion is enabled for **every normally admitted org**. Missing config
files, absent blocks, old off/dry_run/on and newly discovered/admitted orgs all
use the same policy. The obsolete `disk_sweep` key is ignored in every YAML
shape/value; it cannot strand startup or veto fresh admission, contained repair,
the single Git retry. No replacement opt-in, config rewrite or migration
is required. Unrelated configuration validation still refuses invalid settings.
Org discovery/admission, native writer/team-move gates and complete fresh
task/job/Git/age/containment checks remain controlling; refused/broken/unloaded/
deferred orgs never become successful admitted orgs through mode selection.

Explicit manual `--dry-run` inventories even an org with obsolete off config and
performs no chmod, unlink/rmdir, Git remove or prune. Summaries/audits are permitted
reporting writes. `mode` reports actual on/dry_run execution; retired
`configured_mode` is absent. The config digest remains observational.

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
* OS-process usage is **not checked** under Founder seq117. There is no process
  census, namespace/cwd/root/exe/fd/environment eligibility scan, exemption,
  authentication or absence claim, including on fresh repair/retry admission.
  Top-level JSON and org/audit `process_scope` state this policy. The existing
  org `process_exemptions` field remains empty and unused for compatibility;
  no synthetic exemptions are emitted. This supersedes the former four-name
  and zombie rules, not another cleanup owner's process gates. Filesystem
  platform-specific mount containment below remains required.
* A complete no-follow descendant scan, all entries at least3600s old by BOTH
  mtime and ctime; atime is excluded because observations may update it. Root
  mtime alone never admits. Unreadable directories, devices/unknown special
  types, external hardlinks, identity drift and filesystem/mount boundaries
  retain. Sockets/FIFOs are disposable only in Phase1 scratch (seq194);
  worktrees and Git metadata keep strict special-object retention. Exact own
  chmod mode/ctime changes may be recognized on Git retry, with all other gates
  refreshed; unknown changes never receive that exception.

### Indexed current ownership reads (THR307 seq337 step 3, reader portion)

Every existing `Evidence.ownership` call opens its own `mode=ro` connection and
read transaction, checks the actual native writer/optional hold, and then reads
current ownership. Candidate and unfinished-job ancestry use `tasks.id` primary-key
lookups. The row cache exists only within that transaction. SQLite's duplicate
NULL primary-key case keeps the old org-wide conflicting-lineage refusal.
The existing `jobs_status_idx` supports exhaustive distinct-status enumeration
and indexed reads of every unfinished or unknown/NULL-status row. Rows retain
original rowid encounter order, including active-before-unknown refusal priority.
Finished completed/failed/rejected rows never need their historical owner resolved.
The existing audit task-id index supports the unchanged complete workspace check.
Discovery's once-per-clone topology census is also retained privately for KEEP
answers. In existing candidate order after ownership refusals, worktree guard,
known mapping/lock/branch and dirty-status failures are final for the night,
including after healing. Own directory identities and reciprocal pointer
signatures are revalidated no-follow before status; changed/unsafe locations
hold the clone. Positive
mapping/clean-status observations are discarded; every deletion still performs
all existing fresh admissions. Inventory cannot supply successful delete authority.
This partial implementation does not yet reduce destructive census frequency.

Each full worktree admission also revalidates its own directory/pointer identities
against discovery and compares freshly read HEAD/branch with that inventory.
The saved identity is a veto on drift, never a successful deletion answer; every
current topology, ownership, preservation and recursive-tree check still runs.
After non-force removal, the folder must be absent, the registration gone, and
the admitted named branch must freshly resolve as a commit. Detached worktrees
have no named branch to resolve. Failed post-effect resolution keeps the effect
partial/unknown and coverage incomplete under the existing error classification.
The exhaustive literal census proves the primary common directory as repo/.git.
Each gate still resolves the candidate's current common directory and top-level
through Git and checks the literal directory no-follow; it omits only the
redundant Git lookup of that primary common directory. No census is omitted.

During candidate admission, final removal/repair/retry checks and post-removal
confirmation, the census skips repeated reads of a disjoint sibling's ordinary
index/auxiliary file only when its current full stat signature equals discovery.
Changed or arriving files are fully read. Discovery reads them all. Every census
still stats every admin object, checks unsafe types/hardlinks, reads all HEADs
and reciprocal pointers, validates unique locations/ancestry and literal roots,
and reconciles Git's complete list. Candidate/overlapping registration metadata
and unknown-location residue validation remain fully read; siblings' stat signatures
still latch observed changes to their owner. No successful inventory answer is
reused, and no census, containment or fresh admission boundary is removed.

After discovery, one consistent org inventory reads tasks and unfinished/unknown
job states once, with the same workspace/lineage evaluator and refusal priority.
Only its failed answers survive: they are final KEEP decisions even if the
underlying condition later heals. Successful answers are discarded. Every
existing admission still opens its own fresh connection and transaction; neither
task/job inventory nor its earlier native-writer observation authorizes removal,
repair or retry. Inventory failure retains uncertainty rather than implying an
empty census. Destructive pointer/topology admission frequency is unchanged in
this partial implementation; reducing it still requires the complete section 5.2
proof. Final dirty/guard/mapping keeps do not repeat those admissions.
No schema, new index or migration is introduced. Unknown status/active-job lineage
still holds the org; candidate-own ancestry uncertainty still holds the item.

Task/job refusal inventory removes repeated ownership work for final keeps.
The existing fresh delete admissions, census/tree checks, retry, classification,
completion, policy and keys remain. Cheaper full destructive admission and
recursive-age keep screening remain unfinished parts of the approved step 3
package. Two-pass ordering is absent: real-night step 1 data is not yet available.
These partial changes do not establish real-night speed or an under-ten-minute
result.

Worker publication batches up to 32 nonmutating checkpoint requests. The initial
report, first initialized org state, outer run boundaries, every org exit/error/expiry
and every mutation-in-flight
intent flush immediately through the unchanged atomic writer (exclusive pending
inode, literal no-follow parents, descriptor-relative replacement). A removal
intent is persisted before its final fresh admission, so a failed publication
prevents deletion. Suppressed progress writes confer no admission authority;
all ownership, topology, tree, remove/repair/retry and post-effect checks remain.
An interrupted batch may leave older keep/removal progress in an incomplete
report; an unconfirmed intent remains unknown, never a confirmed effect. Only
the existing parent's actual stop/reap proof and safe final publication can make
night.complete true. Reports, org audits and their keys remain compatible.

Admission takes two complete no-follow snapshots with intervening ownership/Git
refresh and final root/Git validation. On-mode mutation repeats the entire
admission, then compares the two admitted trees again. Both comparisons use
exact cardinality, path lookup and the unchanged eight-field signature (device,
inode, type, mode, size, mtime_ns, ctime_ns, nlink), independent of insertion order.
They check the SAME original deadline at entry, before every comparison entry
and at exit, accumulating mismatch without skipping later required work. Missing
partners mark mismatch; full left membership plus equal cardinality excludes
right-only entries. Expiry propagates as incomplete, including after an earlier
mismatch. Unexpired drift retains with `tree_changed_during_admission` or
`tree_changed_before_mutation` at the respective caller.

Direct comparison uses constant additional storage; the complete snapshots,
Counters, seen sets and other sweep costs remain O(N) or otherwise unbounded by
this correction. An in-progress signature pair, syscall and other observation
work is not independently preemptible. Cooperative checks supplement the existing
parent supervision; they certify neither a hard OS-I/O deadline nor speedup,
backlog clearance or physical freed bytes. `mutation_in_flight` is checkpointed
before the final admission and may precede every unlink. A partial removal may
change a surviving root's mtime and subsequent discovery position; ordering
continues to use current root mtime.

Paths open from `/` one component at a time with O_NOFOLLOW. Tree syscalls use
verified directory fds and recorded inode/device identities. Linux retains its
`/proc/self/mountinfo` reader unchanged. Darwin uses native getmntinfo/statfs
against the public XNU xnu-11417.101.15 LP64 statfs ABI (2168 bytes, all field
offsets checked, arm64 or x86_64 INODE64 symbols). A bounded complete native table
and independent root statfs agreement are required; malformed/unavailable evidence
retains with `unknown_mount_evidence`. Other platforms retain. Mount evidence
rejects same-device mounted paths as well as device changes. Internal symlinks
are unlinked
only, never traversed or chmodded; root/ancestor symlinks refuse. Permission repair
adds owner-write on admitted directories through verified fds, never `chmod -R`.
Tree mutations recheck identities and deadline before each syscall. POSIX unlink
is still pathname-based; a hostile same-UID replacement in the final check/syscall
window is not fenced. External hardlinks retain to avoid changing unknown/durable
inodes. Internal hardlinks track own unlink nlink/ctime effects and count once.

S3 frozen cleanup dispatch uses the hidden daemon entry
`happyranch-daemon --cleanup-helper disk-sweep ...`; root worktree helpers use
`--cleanup-helper worktree-safety ...` and `--cleanup-helper check-path-use ...`.
Only these fixed names are accepted, before daemon-home initialization, listener
binding or lifespan startup. Source/uv installs keep Python `-m` for the sweep
worker and Python script paths for the worktree helpers. The two worktree scripts
are release data in the existing bundled-skill inventory; the dispatcher and
sweeper are explicit PyInstaller hidden imports. This fixes invocation only:
unavailable process-use evidence still retains candidates; Darwin scanner parity
and the manual mount-parser repair remain S4 work.

The sweeper revision is `code-sha256:` over loader-provided code for
`disk_sweeper`, `_disk_sweep_fs`, daemon `app` and `orchestrator.org_config`, with
module names framed and nested code filenames normalized. It uses source/wheel
and PyInstaller PYZ loaders without requiring loose `.py` files or Git. The code
identity is Python-bytecode-version-specific, not a source-tree/release SHA.
Missing/unsupported loaders, absent code or read errors still refuse before
worker launch; no placeholder revision authorizes a sweep. Reports/audits retain
the existing revision field. All deletion, stop, ownership and timeout gates stay
in force.

## Phase1: task temporary roots

Only `<workspace>/.happyranch/task-tmp/TASK-*` is discoverable. The current
`task_scratch.prepare_task_scratch` producer creates disposable roots and sibling
`.happyranch/task-scratch-manifests/<task>.json` plus `.lock` as **durable recovery
artifacts**. The existing strict bounded manifest validator supplies additional
classification/root evidence; it is never sole deletion authority. Missing or
invalid manifest retains. Sibling manifests/locks are not candidates or repaired.
Under Founder THR307 seq194 item2 approving seq193, nested Git repositories
and `.git` files/directories are disposable scratch content after BOTH task and
structural root have ended and all other gates pass. Unix sockets and FIFOs
are measured with no-follow stat/allocated inode blocks and unlinked through
verified parent descriptors. They are never opened for endpoint/content I/O,
read, connected, drained or signaled. Linux O_PATH descriptors observe only
inode metadata, including the pinned inode after unlink. Darwin never opens any
leaf (regular files, symlinks, sockets or FIFOs): immediately before contained
unlink it takes a no-follow stat through the verified parent fd and requires the
full current/expected signature. Only after its own successful unlink of an inode
with nlink>1 does it record that identity's expected nlink-1. A later sibling may
differ from its snapshot only in ctime and exactly that recorded nlink; every
other field must match. Unknown identities, stat errors and other drift retain.
Darwin loses post-unlink observation and has a non-atomic stat-to-unlink window;
Linux also has a window between fstat and unlink-by-name. This accepted limit
within the existing same-UID threat model is not an atomic identity fence.
Devices and unknown special types still retain. Both admission
scans and mutation explicitly select the scratch policy; shared defaults, Git
metadata census and every worktree scan/revalidation/repair/remove stay strict.
Internal symlinks remain link-only, with outside targets untouched. `output/`, caches,
`.happyranch/scratch` and any other target are outside candidate discovery.

An admitted scratch operation repairs its contained directory permissions and
uses fd-relative unlink/rmdir for that exact root. It never invokes generic rm.
Failure is recorded partial/unknown and is never retried with more force.

## Phase2: registered task worktrees

Only actual Git registrations exactly under an owner's
`repos/*/.claude/worktrees/TASK-*` qualify. Require matching guard task/worktree/
primary-root ownership, ordinary no-follow primary `.git`, and a complete
`.git/worktrees` inventory reconciled with Git output and literal task-worktree
roots. The common/admin/entry directories and exhaustive file/name census must
be ordinary and unaliased, with canonical unique matching `gitdir`, `commondir`
and worktree `.git` backlinks, apart from the safe unmapped gitdir-ENOENT
residues and locally retained additive TASK arrivals described above. Other
unknown mapping or unsafe metadata refuses the
shared scope before Git; unexplained Git output differences also refuse shared
scope. With this proof, ordinary
local read/change errors protect only their proven registration. A missing HEAD's
actual zero-HEAD/detached Git diagnostic stays protected; it never grants eligibility.
Per-attempt local retention is latched even if the file later heals; this caches no
clearance. Lost attribution widens refusal to the shared repository.
A genuinely absent linked-admin directory with primary-only Git inventory and
no surviving task-worktree roots is a valid empty control. Conservative pointer
validation accepts canonical absolute backlinks and ordinary `../..` or exact
absolute common-dir pointers; other spellings retain unknown. Also require an
unlocked/nonprunable registration, correct owning task branch or detached HEAD,
exact common-dir and toplevel, clean porcelain status including every nonignored untracked file, and
empty `git rev-list -n1 HEAD --not --branches --remotes`. These Git facts are
re-read after slow common inspections. Dirty paths are reported. Unique detached
commits remain. Local branches/remotes preserve committed work without GitHub
publication. Any different linked registration strictly beneath a candidate
retains that containing worktree, including ignored paths, detached HEADs and
nonmatching task-root names. Every proven canonical path in the complete
reciprocal census, including locally uncertain registrations, is consumed with
the existing deadline; discovery's selected
task-root subset is insufficient. Literal path components define ancestry, self
is excluded and siblings such as TASK-1/TASK-10 do not contain one another.
Rule4-prime is unconditional across repositories and orgs, even with no residue
anywhere: either completed existing admission snapshot containing a non-root
entry named exactly `.git` retains the worktree. Files, directories, live and
dangling symlinks at any depth match, including ignored paths. Only the candidate's
own relative root `.git` is exempt. The latched reason is
`possible_nested_worktree:<first sorted literal absolute .git path>` without
resolving links or reading nested content. Existing linked_descendant retains
precedence. Check both snapshots at every initial, fresh pre-removal, pre-repair
and pre-retry admission, before generic snapshot mismatch can mask the reason.
No subsequent removal/repair/retry starts; a prior begun effect remains
partial_or_unknown with its authentic error and earlier allocation if measured.
This consumes existing snapshots only: no scan, provenance input or cross-org
pre-pass. Earlier orgs are therefore protected before any later residue census.
Scratch seq194 is unchanged. Other Git-ignored contents inside an admitted tree
are disposable. “Re-creatable” refers to committed contents only.

Use `git worktree remove` without `--force`; keep branches. After ordinary failure
there is at most one contained permission repair and retry, each with full fresh
eligibility. Registration and containment identities are refreshed immediately
before removal, repair and retry, including after the first Git failure.
The descendant guard consumes each existing final complete-census result before
first remove (before mutation starts), contained repair and single retry, even
when a complete admission just succeeded. There is no alias resolution or new
deletion authority. Valid and locally uncertain mapped descendants retain the
candidate at every final boundary. Candidate-local metadata errors also block its
own remove/repair/retry. Fresh admission binds shared topology/pointer/conflict
facts and the candidate's own metadata; ordinary sibling auxiliary churn does not
poison that equality. An incomplete mapping census still refuses the shared scope.
Local unknowns are reported against their proven worktree and exact metadata
cause, including non-task descendants; discovery stays partial, uncertain totals
and physical freed space stay null, and only known reached/unreached counts are
reported. Any partial/unknown/shared scope or remaining registration prohibits
repository-wide prune. Post-remove credit requires independently confirmed
worktree-root absence and absence of the actual Git registration row, while an
independently retained unmapped safe admin residue, including one at the former
admin pathname, neither blocks nor erases that credit and keeps its unknown
worktree location/allocation, E scope with candidate_count=0 and exact native
gitdir ENOENT reason, partial/incomplete outcome and zero repository-wide prune; a sibling's new local error cannot erase verified
credit, while shared confirmation uncertainty preserves the physical partial effect
without unverified credit. Earlier verified effects retain their actual accounting.
This Git version
can unregister a worktree on permission failure;
that partial/unregistered root fails fresh admission and is retained, with no
re-registration, generic rm or stronger retry. Timeout aborts the entire worker,
so potentially surviving descendants are stopped by its supervisor.

Review decision 2 removes empty-scope clone-wide Git prune entirely. Normal
successful removal still removes its own registration. Missing-folder and
unknown-location residues are retained, never repaired or pruned; neither
contributes removed bytes. No replacement housekeeping runs.

## Reports and operational acceptance

One JSON under `<daemon_home>/disk-sweep/<scheduled-date>.json`, and one existing-
shaped `disk_sweep_summary` audit per loaded org (also when globally suspended) (no table/column change). Manual
runs use a unique manual filename and one permitted audit per applicable org.
Audits keep existing empty task scope/daemon attribution. No per-candidate receipts
or new ledger. Summary includes actual executing source hash, config digest/effective mode,
zone/window/date, discovery status, outcome, retained reasons/paths, dirty paths,
known eligible leftovers, unreached counts/orgs and errors. In-flight checkpoint
survives ordinary worker interruption; report failure is never fabricated success.

### Coverage and classification (THR-307 seq337 package step 2)

This is the approved behavior change after the measurement-only baseline:
outcome labels use coverage and empty-scope prune no longer runs. The three
censuses per topology call, candidate admission, final ownership, nested `.git`,
containment, non-force removal, retry, timeout and stop policy are unchanged.
Removing prune also removes its one final topology call; no admission proof is
removed. `.worktree-guard.json` ignore and cost reduction remain later steps.

`_CLASSIFICATION` is the authoritative scope/coverage table:

| Finding | Hold | Coverage |
| --- | --- | --- |
| Proven mapped ordinary index/HEAD/auxiliary read/change error (including sibling churn) | Owner only | Located inspected keep |
| Fully proven arriving TASK mapping | Arrival only | Listed deferred arrival |
| Safe admin entry with missing `gitdir` | Entry only, never repaired | Unknown location ALWAYS blocks complete |
| Unique recorded mapping with genuinely absent final folder | Entry/location only, never repaired | Located keep after full proof |
| Unregistered TASK folder | That path | Conservatively unknown coverage |
| Positively classified ordinary non-clone folder | Excluded | Listed exclusion |
| Unknown/conflicting mapping, enumeration or ancestry | Whole clone | Not complete |
| Candidate-own missing/cyclic/malformed task ancestry | Candidate only | Not complete |
| Unknown job status or active-job lineage | Whole org | Not complete |
| DB/native-writer failure, Git timeout | Org/run | Not complete |
| Known dirty/active/recent/hardlink/mount/nested/descendant keep | Item | Located inspected keep |
| Other unclassified observation | Existing conservative retention | Not complete |

A missing folder qualifies only for final-name ENOENT through a readable literal
parent, with canonical unique recorded `gitdir`, valid `commondir`, safe remaining
metadata, all existing censuses, Git-list reconciliation and stable ancestry.
Its location stays in the linked-path descendant guard even outside conventional
TASK roots. Aliases, conflicts, unreadable parents, reappearance or re-pointing
refuse shared scope and cannot clear its latch that night. Its absent backlink
is not fabricated. No repair/prune/re-registration is attempted.

Ordinary non-clone exclusion requires a plain no-follow directory, final `.git`
absence, a readable valid owner agent definition showing it is not configured,
and no `.claude` residue suggesting a former clone. Missing/malformed agent
metadata conservatively leaves it unknown. Unreadable/aliased/special `.git` and
a configured clone whose `.git` disappeared never become exclusions. These are
sampled observations under the existing same-user model, not a rename fence.

Each org records `inventory_window` and `coverage` (`complete`, reasons,
`unknown_discovery`, candidate count and unreached count, null when unknown),
`scope_classifications`, exclusions and deferred arrivals. Legacy `discovery`
remains partial for local errors; legacy counts remain known-only/null. Coverage
states when the scope is fully located despite such a legacy partial label.
The date JSON and matching org audit carry `night` with definition
`thr307-review3-coverage`, Boolean complete and explicit reasons, policy, source
revision and elapsed seconds. A complete scheduled night requires every selected
and admitted org reached, mode on, completed inventory/window, every candidate
removed or a final located inspected keep, and no unknown discovery/shared hold,
unreached/in-flight/partial effect, operational error, deferred/unloaded org or
unconfirmed removal. Stop/reap are within the original 1,800 seconds; a late or
unproven closure is not complete. Worker-only final JSON has
`night.complete:false` with `supervisor_closure_unproven`; its `outcome` describes
worker coverage only. The parent records `worker_group_stop` and integer
`worker_returncode:0` after owned stop/reap, then recalculates the night. Both
fields also appear in summary audits. The prior-night check requires matching
summary audit payloads for every loaded org before suppressing an incomplete-night
record; missing, mismatched or unavailable audits remain incomplete.
A failed publication is never claimed complete;
only a safely published value can establish the record. Preview is not a complete
scheduled night. Legacy summaries without these facts remain unproven.

Existing checkpoints persist `interrupted_removals` for in-flight/partial intents:
path, phase, state, reason, unknown effect and `automatic_recovery:false`. An
intent may precede actual mutation; it proves no removal. The existing bounded
history pass carries at most 256 direct interrupted candidate records from the
reports already read into `previous_interrupted_removals`, with source report,
night date and source revision. Exceeding that limit makes history unavailable;
it does not grant clearance. History/report publication uses existing no-follow
safety and byte/count limits. These fields only report provenance; they never
feed admission, repair, finishing or retry. Kills between checkpoints retain
unknown effects, not a complete operation log or data-loss audit.

At daemon startup the last expected machine-local 03:00 is today's date at/after
03:00, yesterday before it. Before each existing scheduled night, the check uses
the preceding date. Only that previous expected date is checked; there is no
historical missing-night scan or extra timer. Missing/incomplete summaries produce
one durable `disk_sweep_night_notice` per affected night per loaded org through
the existing audit interface (`task_id:''`, actor `daemon`), plus a daemon warning.
Existing committed notice rows deduplicate restarts/repeated checks under the
single daemon scheduler. Failed audit publication logs failure, never success;
an unavailable org cannot persist a row until loaded. The payload gives date,
missing/incomplete, reasons, and source report path when present. This is a
**record, not delivery to the founder**. Delivery is pending a founder choice at
THR-307 seq342; no thread, UI, identity, route, schedule or monitor is added.
A daemon that stays down cannot record. Assumption: stop-file suspension is not
complete because no coverage occurred; `scheduled_suspension` is its own reason.
No catch-up, retry, marker reset, stop-control or run-admission change follows.

Disposable driver C27–C30 cover coverage/table boundaries, missing-folder peer
progress and containing-tree protection, startup/restart/pre-night persistence
and deduplication, and interrupted-removal reporting without recovery. C27
separates candidate-own ancestry from genuine unknown job lineage; C29 drives
the real scheduled child/supervisor path with loss between worker and parent
publications, and between parent publication and summary audits, plus a fully
closed run. These closure checks do not consume or alter the 256-record
interrupted-removal history cap. C26 sizes
valid reports from their actual serialized bytes (still >32 MiB aggregate),
retaining bounded-window/kept-since and malformed/oversized boundary assertions.
C23 expectations distinguish proven arrivals from unregistered paths. C21/C25
expect no prune or its final census; earlier removal attribution remains required.
Real-night speed, under-ten-minute target, job liveness, absence of data loss,
Darwin and seven scheduled complete nights remain unproven by these fixtures.

### Measurement-only additions (THR-307 seq337, package step 1)

At step 1, eligibility, ordering, reasons, outcome labels, holds, latches, prune, 15-second
Git timeout, shared 1800-second budget, schedule, stop control, native writers,
publication safety and existing report/audit keys keep their meanings. No
candidate filesystem/Git/DB observation is added. The existing `revision`
remains the executing loader-code binding, including frozen helpers. Additive
`source_revision` carries that same binding in reports/audits; it is not a release
Git SHA. No Git revision lookup or loose-source fallback is introduced.

Each candidate has an additive `measurement`: `stage_reached`, monotonic
`duration_seconds`, `final_reason` (at most 1024 characters, with
`final_reason_truncated`; the full existing `reason` remains),
`attempted_effect` and `confirmed_effect`. Removal attempts count actual scratch
mutation/Git remove entries; confirmed removal requires the existing absence
and unregistration checks. A repair attempt counts entry to contained repair;
confirmed repair counts returned repaired directories. These counts do not grant
clearance. Eligible preview has no attempted mutation. Unreached detail stays
at `discovered`/zero time with no effect claim. A started item carries
`started_monotonic` so supervision can finish elapsed timing on interruption.
The saved stage/effect counts are checkpoint observations: a killed worker may
have progressed since then. Supervision marks `effects_after_checkpoint:
unknown`, never invents an attempt, removal or successful repair, and retains
the existing candidate state and partial-mutation uncertainty.

Each org has `measurements` with elapsed time, bounded error count/eight error
type samples, history status, `counters` and a path-keyed `clones` aggregate.
Counters cover `topology_reads` (registrations calls), `censuses` (literal metadata
census calls), `ownership_reads`, `entries_walked`, and `report_writes` plus
`report_writes_confirmed`. Entry counts are snapshot root/child visits and
mutation visits, including visits before a refusal; metadata-file reads and
ancestor traversals are not claimed as tree entries. `git` groups actual process
calls by subcommand (`worktree list/remove/prune`, `rev-parse`, `rev-list`,
`status`), with count, monotonic duration and timeout count; no argv or call log
is added. `durations_seconds` groups the existing call boundaries, including
failed calls. Nested timings overlap and must not be summed as exclusive time.
Clone counters attribute candidate operations and topology calls to that clone,
including primary-only prune. Scratch and shared checkpoints without an active
clone contribute only to org counters. Org timing/counters cover `sweep_org`,
not supervisor reservation/reap or outer run-level publications. Call counts
include failed entries; Git counts begin immediately before the existing process
invocation, after its original deadline check.

`gitdir_gone_count` is the latest successfully completed census's count of
unknown-location admin residues, excluding located unregistered TASK folders.
It is never multiplied by repeated censuses. Without a completed census the
count is the literal `unknown`; org totals are unknown if any observed clone
count is unknown. This is sampled evidence, not a claim about unenumerated
clones or current state after that census.

`stop_cause` is additive at run/org level: `finished`, `budget`, `git_timeout`,
`error`, or supervisor `shutdown_signal`; `in_progress` describes an unfinished
checkpoint and `stop_control` describes scheduled suspension. Existing
`outcome`, `error`, `interruption` and audit envelope/keys remain authoritative
and unchanged. A finished traversal may still have an incomplete discovery or
retained candidates. A caught operational/configuration error records `error`
even when traversal returns normally; ordinary partial discovery alone does not.
Instrumentation errors are reported and cannot authorize,
refuse or fail an operation. Timings use a separate monotonic performance clock
and do not modify the original deadline checks.

`kept_since` is the earliest recorded retained date for the exact path within
report history actually read, including safe carried earlier dates, not an inode
identity, proof of continuous retention or claim beyond that window. History is
reduced once per worker run, before discovery, through literal no-follow report
parents/files: at most 512 directory entries, 32 preceding date reports, 8 MiB per
report, 32 MiB total and 8192 paths. Reaching the report-count or aggregate-byte
boundary is a successful truncated window: validated dates survive, and
`measurement_history.status=bounded_window` visibly reports truncation alongside
`reports` (fully validated reports) and `bytes_read` (content bytes read, including
any later invalid read). The next report is not read if it exceeds the remaining
aggregate allowance; its type/link and independent 8 MiB limit still apply.
The current run date is the first observation when no prior date is available.
Manual filenames are excluded. Genuinely invalid/unsafe/individually oversized
reports or other history failures expose `unavailable:<exception type>`, discard
the partial lookup and never change a sweep decision.
`kept_allocated_bytes` comes only from an already completed candidate snapshot,
inode-deduplicated from its existing stat objects; otherwise it is the literal
`unknown`. Partial walks cannot establish a size. This does not change the
legacy eligible-only allocation counters or claim physical freed bytes.

No measurement-only checkpoint is added. Counters are published as value
snapshots: write attempts include the publishing call, while confirmation and
write duration end at the previous completed checkpoint. A write cannot record
its own completion without another write. Worker death can lose observations
since the last checkpoint, which remain unknown. Candidate detail lives in the
date/manual report and in the unchanged audit `org` projection. The direct
`AuditMixin.insert_audit_log` writer has no payload cap, so no existing key is
removed or reinterpreted. Output adds constant detail per item and aggregate
subcommand counters rather than a per-call/event stream. C24 measures a real
fixture shape expanded to 1600 dirty-kept items/12 clone aggregates (about
1.95 MB date JSON and 1.52 MB audit JSON; exact bytes recorded in PR evidence).
This is a synthetic size measurement, not a production run or runtime forecast.

The existing disposable driver owns cases 24 (byte-equivalent legacy report/
audit projection against pinned pre-instrumentation source, including eligible
preview and caught configuration errors) and 25 (known call counts, effects,
kept size/date, report alias refusal and forced recorder failure). Case 26 drives
32 production-sized reports through the real reader, checks aggregate truncation
without lost dates and verifies malformed/oversized report visibility. Case 12 adds
budget/shutdown cause and interrupted
measurement assertions to its existing actual worker/process tests. Focused:
`.venv/bin/python scripts/verify_disk_sweeper.py --cases 12 24 25` with task-owned
`TMPDIR`; full driver and lint/static verification use durable jobs. No retired
Python units or ad-hoc Product E2E are introduced. Darwin native/frozen-binary
execution still requires its own venue; source dispatch and helper argv are
unchanged.

The three local `metadata_changed:<exact native path>` comparisons append compact
`;signature_change={"comparison":...,"changes":...}` JSON. Fixed comparison labels
are `snapshot_to_read`, `pre_git_censuses` and `post_git_census`; `changes` contains
only differing old/new integer pairs, in signature order: device, inode, type,
mode, size, mtime_ns, ctime_ns, nlink. A missing observation is null, so at most
eight pairs occur. These are the already compared observations, with no new I/O,
contents, atime, capture timestamp or history. Equal observations add no reason.
Earlier native causes stay latched; within-read and shared errors keep their
existing text. The prefix/category and every report/audit key remain unchanged;
the audit carries the same updated org object. Deltas identify neither a writer
nor data loss and grant no eligibility or clearance.

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
The maker performs no live cleanup, config write, deployment or restart. Root
TASK12353 owns reviewed guarded merge, exact-main static checks, normal rollout
and serving-source verification that every admitted org uses scheduled deletion
independently of config. Founder seq134/136 already authorizes activation after
delivery, without another decision; source publication alone does not prove
loaded-source activation or actual reclamation. No manual cleanup proof follows.
Seven actual on-night records of attributable results, retained/unknown/eligible backlog and errors precede separately approved cutover.
~50GiB is provisional; output growth or operator deletions cannot determine PASS.

## Finite disposable verification

ROOT13983's registration-isolation correction extends the existing real-Git driver
with C19-R/L/E/U/P, C20-U and C21-N/T/P. R runs first within c19: the actual census
observes Git's pending index.lock, the original reader reaches that exact inode,
then the exact writer exits0/is reaped and its lock disappears before the real read.
Only the subsequent B physical-removal/administrative-unregistration assertion is
the intended baseline failure. No synthetic exception, manual lock deletion or
setup failure is a witness. Writer cleanup stays inside fixture lifecycle on every
exit; genuine index rewrite is a separate Git fixture effect. Local index EACCES,
missing HEAD and actual changed auxiliary note protect A while eligible B progresses;
owner/root/jobs/dirty C remains protected. Unsafe mapping/hardlink/duplicate controls
refuse before Git. Canonical uncertain descendants cover final_to_remove,
retry_to_repair and repaired_to_retry, preserving all five valid transitions and
actual earlier failure/repair effects. Ordered post-remove cases independently
confirm B effects before testing local versus shared A damage. Preview covers only
reachable static/discovery states, and the local JSON/existing audit projection
retains scoped causes and honest unknown counts. C17/C22 keep locked/primary-only
and original-deadline controls. Focused selection (frozen Python3.14/Node24/Git,
ordinary UID, actual task-tmp; outside retired units/general integration):

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 /absolute/locked-env/bin/python scripts/verify_disk_sweeper.py --cases 7 8 10 17 19 20 21 22
```

ROOT13755's comparison correction extends existing c12 with tiny real Fixture
rows at BOTH admission and cross-admission comparisons: reordered equal captures
must permit real removal with independent lstat/inode allocation; same-count late
write, key rename, replacement and chmod must retain with the exact caller reason
and preserved changed contents/controls. These rows do not isolate all eight
signature fields from accompanying ancestor changes; exact parity is also reviewed.
CPU-expiry rows arm only after genuine selected snapshots/admissions, at entry,
first pair, last pair/exit and an early non-root mismatch followed by later expiry,
each exactly at the ORIGINAL deadline and beyond it. Delegating observers bind
old `ds.signature`, new `fs.signature`/`fs.check` and imported `ds.trees_equal`;
they record entries begun or normal comparator returns after expiry, before outer
guards can mask a missing exit check. They add no guard or fabricated Expired.
Resource assertions accompany actual incomplete reports, preserved filesystem/
sidecars/outputs/outside controls, no later admission/mutation/Git action and
truthful pre/post-measurement allocation with zero removal credit. Empty maps
are defensive source-reviewed semantics only: successful snapshots include `''`.
Existing c12 partial/actual-unlink supervision assertions remain. Both blocked-Git
supervision rows require a real runnable sleep child in the owned worker group
within one second of fixture launch, before the ORIGINAL two-second total budget
expires. Startup/import/checkpoint work consumes that same budget; readiness does
not renew it. The rows assert actual deadline versus shutdown, responsive heartbeat,
leader reaping/child stop, second org unreached and truthful persisted incomplete/
partial accounting when publication is safe. Unsafe pending publication remains
separately owned by `correction_stop_invalid`/`publication_refusal`: visible refusal
and preserved pending evidence, with no promise of a final summary under every kill
timing. Focused supported selection:

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --cases 1 12 19 21
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --correction-cases 6
```

Use frozen Python3.14 and ordinary UID. c1 owns allocation, c19/c21 shared
worktree admission/recovery and C6 (`correction_fresh_policy`, not historical
`--cases 6`) fresh policy keepers. c9/c10/c11 retain missing/unknown, active-job/
retry and recent nested-age coverage; c14 owns discovery exhaustion, not CPU
comparison expiry. No historical namespace/host execution, scaled fixture or new
diagnostic/performance campaign follows. Record actual execution/proof limits;
source readiness and a small fixture do not close nightly operational acceptance.

TASK13199 / ROOT13198 uses the existing driver with `--cases 1 2 7 8 11 12
16 19 20 21 22` and `--correction-cases 1 2 6` in separate invocations, under
actual task-owned TMPDIR with Python3.14 after `uv sync --frozen --python 3.14`.
Cases1/2 cover each scratch allowance separately and combined, independent
lstat allocation, durable controls, outside links and active owner/root refusal.
Cases7/8 cover strict ignored worktree endpoints and metadata, safe synthetic
device/unknown-type observations (no mknod), and unchanged dry-run contents,
permissions, Git refs/registrations and outside sentinel. Case16 includes literal
root/ancestor link refusal; retained age, fresh jobs, registration, partial-error
and deadline cases exercise shared-helper preservation. No historical namespace
or mounted-boundary case is selected; those remain SKIPPED/UNEXECUTED. No new
harness, retired Python-unit import/collection, integration or Product E2E run.

Historical broader driver (not authorized for the current policy delivery):
`.venv/bin/python scripts/verify_disk_sweeper.py` after `uv sync --frozen`.
This standalone script imports no retired unit selection and uses real files,
Git and processes with synthetic task/job/audit projections. An advanced wall
clock makes fresh fixture ctimes old; monotonic budgets remain real. Historical
process-only cases3/4 are SUPERSEDED and excluded from active selection. The
former source described cases1–14: eligible root with durable controls/hardlink accounting;
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

TASK-13300 extends c19/c21 for the accepted registered-descendant contract:
C1/C5 use a real same-primary detached `outer/.claude/worktrees/TASK-1-baseline`,
distinct retained base/outer HEADs, fixture-primary `.git/info/exclude` prepared
before snapshots, and otherwise ordinary clean/old/eligible gates. C1 retains
both trees and all metadata with zero remove/repair/prune; C5 uses mutation
sentinels and real dry-run JSON/audit projection. C2 removes eligible TASK-1 with
positive allocation attribution while locked sibling TASK-10 and refs survive.
C3 introduces real registrations at exactly five boundaries: initial-to-final
admission, final admission-to-pre-remove census, failed remove-to-retry admission,
retry admission-to-pre-repair census, repaired admission-to-pre-retry census.
Expected remove/repair attempts are respectively 0/0, 0/0, 1/0, 1/0, 1/1 with
no retry; b/d/e reach the last census rather than an unrelated age/dirty veto.
Actual earlier failures/owned mode changes remain partial/unknown, never removed
attribution. C4 moves the C1 nested path without updating its backlink: repository
unknown/null counts, no unsafe Git consumption, independent safe progress, then
exact fixture move restoration and bytes/types/modes/backlinks/HEAD/full refs/
porcelain comparison. ctime restoration is not claimed. Shared ignores, original
keepers and the runner/selector remain unchanged. Scoped execution is:

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --cases 19 21
```

All disposable mutation roots must be inside that actual child's task-tmp.
Actual mount/chroot isolation availability
and any missing cases must be reported, not laundered into PASS. No protected
mutation proof or historical/real cleanup runs here. General integration remains
SKIPPED24342, Python units RETIRED, Product E2E daily-only. Independent full code
review and independently executed scoped QA remain required after publication.


### Focused recovery fixtures and isolated boundaries

ROOT12353 correction cases8–12 use isolated daemon_home and multiple disposable
admitted orgs: S1 absent/false default deletion across legacy off/missing/new;
S2 true gives zero reservation/worker/mutation plus actual scheduled-loop marker
and persisted audit; S3 strict JSON/type/key/UTF8/size, OS denial, aliases/special
files and controlled changed/short/read-error observations suspend with a cause;
S4 true/invalid repair affects a separate next admission, with the first result
retained; S5 actual manual subprocess previews with true/invalid/absent controls
stay nonmutating. C5 continues to own eligible preview mutation sentinels. S1/S4
use actual disposable sweep/Git effects with SIMULATED worker supervision, not
native startup or installed-layout acceptance. S2 uses the actual audit writer.
Controlled OS errors are test-side only and distinguished from real unreadable
file denial. No live file/activation/cleanup, new schedule, in-flight-stop claim,
retired units, historical full driver or mount/process-root execution is involved.
The finite cases/four answers and causal RED/GREEN/repetitions are maker evidence;
independent FULL review/scoped behavioral QA and root rollout remain separate.

Historical QA TASK11530 terminal-job lineage refusals are superseded by
Founder seq85. The maintained case9/10 lineage refusal helpers now cover active
jobs only; their other unknown-status, active scope and retry checks remain.
These broad historical cases are NOT authorized for this correction delivery.

The current corrections lane retains C1–C3 (seq117) and adds the finite C4–C7
policy cases (seq134), plus S1–S5 (selector8–12) for scheduled control admission.
S3 also exercises the real scheduler with its actual date-summary/marker paths.
Its existing publication table covers final summary symlink/dangling/hardlink
and existing summary-pending symlink/dangling/hardlink/regular/FIFO entries,
each with false and genuinely absent stop control, through both the shipping
loop and direct scheduled entry. Delegating observers count actual native writer
entries; every unsafe case requires zero reservations/worker effects, unchanged
candidate bytes/modes/refs/registrations and sidecar identities, visible refusal,
and no fictitious summary/audit. The loop retains its date marker. Separate
worker-entry rows retain their independent pre-spawn checkpoint risk.
It also covers parent and ancestor aliases into disposable ended-task scratch, ambiguous marker/
pending entries, and separate scheduled/worker-entry publication refusal. Candidate
bytes/modes, sidecars, refs/registrations and absence of native work remain observed;
unavailable summaries/audits are not claimed. S6 reuses C1–C7 safety assertions. Run from the guarded worktree after
`uv sync --frozen --python 3.14`, with task-owned disposable scratch:

```bash
TMPDIR=/absolute/actual-child-task-tmp PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_disk_sweeper.py --corrections-only
```

C1 exercises both shipping dry-run phases with finished orphan jobs unchanged,
active orphan/unknown status refusal and a valid active job in the structural
root. C2 exercises superseded owner AND root eligibility with actual allocation
and unchanged disposable files/Git registrations, paired active/escalated/unknown
owner/root refusals. C3 exercises ended owner/root eligibility in BOTH shipping
dry-run phases through the actual top-level wrapper, config loading, full
serialized JSON and `_audit_payload`. Python syscall sentinels record and assert
zero attempted process enumeration/reference reads, including swallowed failures,
while allowing and observing real Linux `/proc/self/mountinfo` containment. The SAME
case pairs terminal positives with dirty-worktree and recent-write refusals;
positive measured allocation and zero removed bytes are asserted. Candidate
files/modes, registrations/branches and excluded output/manifest/lock siblings
stay unchanged by each sweep. The actual JSON and org/audit projection report
process usage unchecked with no applied exemptions. C1/C2 keep their task/job
semantics without the obsolete synthetic proc-reader dependency. Task/job SQLite
inputs and the advanced wall clock are synthetic; filesystem/Git/config gates,
JSON serialization and audit projection are real. C1–C3 establish no actual host
namespace, OS-denial, on-mode behavior or operational acceptance. Their historical
missing baseline/defect/repetition proof stays UNEXECUTED, not test-authoring PASS.
The corrections selector excludes all historical/isolation cases. C4 exercises
retired-config compatibility checks; C1–C3 retain their original behavioral
contracts, with C3 metadata updated to the actual effective-mode contract. General
integration SKIPPED24342; seq62 mount/process-root fixtures SKIPPED/UNEXECUTED.

C4 uses the production parser and sweep wrapper across missing/absent/legacy
off/dry_run/on and obsolete null/boolean/unknown/extra/scalar input. Both aged
scratch and registered clean local-ref-backed worktrees are actually removed;
branches, output and manifest/lock siblings survive. The missing-file org is
discovered by the actual runtime-root wrapper. Unrelated config errors refuse.
C5 checks every variant as explicit preview with zero chmod/remove/prune, actual
serialized JSON and persisted existing-shaped audit, plus byte/mode/registration/
branch/output/sidecar snapshots and public legacy-off CLI dry-run/refusal.
C6 changes task/root/job facts at the real persisted pre-mutation checkpoint,
proving fresh refusal. Obsolete config changes cannot veto valid admission or
contained repair/the one retry after an actual controlled Git exit129; a new
active job still refuses repair/retry and preserves partial attribution.
C7 uses actual scheduled reservation and missing-native-owner deferral plus
actual partial discovery/serialized JSON/audit projection. A partial run keeps
its native disposition uncertain; successful per-org closure can settle while
a separate deferred org leaves the batch incomplete. Worker process supervision
is explicitly simulated for this in-process fixture, not verified.

For a finding-specific selection use `--correction-cases 4 5 6 7` in the same
corrections lane. These standalone commands are not automatic PR/merge tests.
Synthetic task/job rows and old-ctime wall clock are labeled; Git/filesystem/
parser/summary/audit work is real. Finite RED against unchanged pre-policy
production and candidate GREEN are required in the PR case record; no protected
gate-mutation campaign, historical process/mount fixtures or broad suite follows.

The following existing isolation source describes separate historical coverage;
it is not execution authority for this policy delivery.

Historical isolation source below describes actual executable fixtures, not
`unshare true`. The process-root expectation is SUPERSEDED by seq117; supported
root selection refuses execution and preserves its source/evidence. Mount source
is unchanged. Neither fixture is authorized for this delivery:

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

The historical root fixture source launched a live same-effective-UID child,
chrooted it into each candidate and left cwd/exe/fds/temp environment outside.
Its process_use retention assertion is SUPERSEDED by seq117 and cannot be
selected for execution; it is preserved solely as historical source/evidence.
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


## Retained C3 native-writer composition (TASK11345 / Founder296seq135)

The scheduled daemon disk worker reserves a global native writer per admitted
org before spawn, through supervision/reap/report. Held/unowned orgs defer with
bounded codes in the existing summary/audit; other admitted orgs continue. On-mode
child admission and prune re-read the actual parent's active global writer ID
through strict append-only history and validate the optional move layout/hold.
An installed layout with missing/unknown ownership refuses before mutation; absent
layout preserves native behavior. Missing/incomplete effect closure, interruption
and partial mutation retain unknown writer evidence; a clean later provider result
cannot erase it. The common1800-second window includes reservation admission.
THR307seq134 scheduled deletion covers every admitted org; metadata/jobs/age/
containment safeguards and explicit manual dry-run remain. This C3 source
checkpoint grants no live cleanup authority. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. Founder THR296 seq316 retired team moves; PR-2 removes
the online operator surface and D operation/recovery/startup guards. The earlier
APPLY_AVAILABLE and D/E/F readiness statements are historical. The C3 native-writer
admission, optional-layout/hold and uncertainty gates above remain live pending
the separate PR-3 removal. The org-owned compatibility table and frozen release
schema digest remain; this cleanup grants no live operation or residue repair.


## Agent lifecycle awareness under seq117

Both bundled start-task and workspace-cleanup guidance warn that nightly runtime
sweeping may remove task scratch and eligible worktrees after the owner task and
structural root end. Agents stop and reap task-owned processes before successful
completion, settle owned jobs and do not depend on those folders afterward. A
genuine authorized native job wait uses existing blocked/wait callbacks, never
fake completion. Durable output stays excluded. This adds no agent deletion
authority or end-of-task deletion hook and changes no legacy/root-terminal process
gates, flags or seven nightly agent sessions. Missing mount/process-root execution
remains SKIPPED/UNEXECUTED under seq62; the old process-retention assertion is
superseded, not newly validated. Protected proof remains unauthorized.
