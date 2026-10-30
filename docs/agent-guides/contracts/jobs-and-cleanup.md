# Jobs, disk sweeping and workspace cleanup contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

## Jobs — automatic admission (THR309seq1)

Obtain required content authorization in a thread BEFORE submission. From a
genuine ordinary task/session omit review_required or set false; rationale is
optional. Parsed true refuses422 before allocation (CLI exit2), without silent
flip/retry. HTTP201/CLI exit0 is admission, never script success; inspect actual
reason/exit/output. Legacy true pending rows/history stay inert and visible as
disposition backlog. Manual /run is a nonexecuting410 tombstone; both CLI run
aliases exit2 and both Web details retain only pending Reject/running Stop.
Initial cwd/interpreter preflight allocates no job. Later authenticated false,
never-launched prerequisite loss alone may commit failed/spawn_failed with
NULL start/exit, genuine finish/diagnostic/agent audit and postcommit wake.
Preserve owner/session/Pause/possible-launch/race winners, existing running CAS,
limits and all-terminal resumption. No schema/auth/permission/credential/general
notifier change or deployment follows. Current contract: [Jobs](../../superpowers/specs/2026-05-26-jobs-design.md).


Stop200 means an actual owned Stop signal request, never exit. CLI says “Stop
requested”; inspect show/wait for the actual row/reason/nullable exit and output.
Running without attributable ownership yields pending/finalizing/unknown/held or
owner-unavailable409; signal/audit503 states request_issued true/false/null. Do not
blindly retry. Startup retains unproven running jobs with explicit unknown liveness;
missing PID/maps, daemon death, timeout and absent native layout prove no orphanhood.
Actual DB attachment/original preparation/attempt/runner/Process fences prevent
cross-org ID collisions and stale-owner signals/removal/writes. The original job
row/CAS, one trigger audit, actual producer and strict Pause disposition commit
before local release/hint/wake; failed tails and descendant uncertainty remain.
Output is directly awaited by the two owned stream pumps, with fresh identity
fences and no per-line Task, intermediate queue or durable-history replay.
Actual buffered tails finish before terminal composition; error/cancel tails
are joined or retain uncertainty. Failed live publication is logged, preserves
file capture and the real result/first cause, and cannot certify retirement.
Wait/SSE use durable terminal reads. Both Web details preserve cached details/logs
on read failure and offer explicit fresh detail-only Refresh status when streaming
is unavailable. No automatic reconnect/polling addition; reopen/reload separately.
Stop success/error settlement refreshes actual detail/list; locale changes issue
no requests. A terminal hint alone never verifies [done]. Source publication is separate
from deployment or historical recovery.

**Independent THR307 nightly disk sweeper — Founder seq117/134, ROOT TASK12353 recovery.**
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

`runtime/daemon/disk_sweeper.py` runs machine-local03:00 deletion for every normally
admitted org, sequentially under one1800s monotonic batch window, with an owned
stoppable worker process group. Only actual terminal-owner/terminal-root scratch
and registered clean retained-ref worktrees can pass fresh jobs/recursive-age/containment
checks; unknown retains. THR307seq85/88 includes superseded for BOTH owner/root
and skips recognized finished jobs before owner-lineage lookup. Founder seq117
removes OS-process eligibility for this nightly sweeper, including namespace,
cwd/root/exe/fd/environment inspection and the former four-name/zombie policy.
JSON and org audit `process_scope` say usage is not checked; `process_exemptions`
is an unused empty compatibility field. Filesystem `/proc/self/mountinfo`
containment remains. Other cleanup owners retain their own process gates.
Under Founder THR307 seq194 item2, nested Git repositories and `.git` files/
directories, Unix sockets and FIFOs inside admitted task scratch are disposable
after both task and structural root end. Socket/FIFO handling uses no-follow
stat/allocation and contained unlink with metadata-only O_PATH descriptors,
never endpoint/content I/O. Devices/unknown special types retain. Worktrees and
Git metadata keep strict special-object checks; all ownership/jobs/age/link/
mount gates and durable manifest/lock/output exclusions remain.
Any different linked registration strictly beneath a worktree candidate retains
its containing candidate, even if ignored, detached or not named as a task root.
Use the complete reciprocal census at admission and immediately before Git
remove, contained repair and single retry; siblings do not veto one another.
Preserve manifests/locks/output, dirty worktrees and
branches. No legacy receipt/count/rotation policy is imported into this new
mechanism; legacy cleanup, flags and seven sessions are unchanged. Dry run never
chmods/removes/prunes. One JSON/existing-shaped audit reports attributable allocated
removals and unknown/partial/unreached scope, never physical freed-byte invention.
Obsolete `disk_sweep` input of any shape is ignored; unrelated config validation
stays active. Effective `mode` is on for scheduled work and dry_run only for an
explicit preview; reports no longer carry `configured_mode`. Founder seq134/136
authorizes all-org activation after reviewed delivery without another activation
decision; root TASK12353 owns normal rollout/serving verification and seven actual
on-night receipts. Source publication alone does not change loaded code. Original
TASK11742/JOB4898 recovery and root11948/PR1128 remain separate. Read the [current contract](../../superpowers/specs/2026-10-15-nightly-disk-sweeper.md)
and runtime guide before changing it; independent review/scoped disposable QA and
normal hooks/static checks remain mandatory. Python units stay retired.

**Current THR-259 workspace-cleanup safety contract.** The shared cleanup procedure accepts only a closed-schema, non-truncated host-job receipt binding the current task/session to the actual job, agent, stored command, interpreter, resolved cwd, timestamps, terminal result, complete output totals, and exact scanner coverage; it has no direct fallback. PR evidence is completely paginated and repeated, and open, closed-unmerged, duplicate, changing, conflicting, or malformed rows refuse. A removable containing worktree must be registered at its owning primary checkout's exact `.claude/worktrees/<TASK>` path on `task/<TASK>`. A cache must be positively Git-ignored, untracked, and absent from status before isolation. Before action and again at the action boundary, the literal candidate is completely walked without following symlinks; nested mounts, cross-device or foreign-owned entries, protected descendants, unreadable/capped/changing evidence, and identity drift refuse. The only external-link exception is a recorded `python`/`python3`/`python3.N` link directly under an owned literal `.venv/bin`, resolving to its configured uv store or `pyvenv.cfg` home outside every protected/workspace/candidate root; deletion unlinks and never follows it. Measurement includes the root inode, and success requires literal absence plus unchanged protected-path identities. Failed restoration after isolation is measured `isolation_anomaly`; failure after deletion starts is measured `removed_with_anomaly`. Both are exit-3 batch halts, never refusals, and account for original, isolated, and isolation-directory residuals without false zeroes. Batch resume accepts only a unique closed-schema terminal row exactly bound to the current manifest and argv; timeout, signal, malformed/mismatched output, unreceipted nonzero exit, runner exception, exit 3, or any unclassifiable outcome halts after journaling and before another candidate. Preparation compares explicit aware terminal instants with exact inclusive604800/86400-second floors and both action-time rechecks; naive/missing/malformed/changed times refuse. The packaged preparation helper freezes cap40/historical overlap exclusions without refill and distinguishes useful, honest complete empty/no-repo, and incomplete evidence. The packaged outer observer validates one supported request with omitted/relative cwd and actual remaining budget, never submits/retries, requires readable self host-init/unstacked-proc and protected metadata including workspace .codex/.aws without content reads, and adds no root-PID1 namespace-equality veto. Baseline failure admits no default packaged batch; batch and postcheck failures/drift retain both native causes and stay nonzero/incomplete. Empty leaves preserve native receipts without fabricated host baselines. Failed/unpublished authoritative lifecycle overrides local success/results; the genuine bound callback is the final session action. Known-excluded nonempty inventory requires verified same-bound preparation, original/copied inventory and manifest/history hashes, owner/native receipts and consistent excluded rows for no-action completion; state labels alone never suffice. Admitted result/pending facts do not bypass missing/failed inventory, baseline, postcheck or write evidence. Native batch argv/cwd/exit/raw is retained before fallible postwrites; after/sequence write failure stays unknown/nonzero with source/binding and both causes, using bounded captured cleanup_sequence_fallback JSON when sequence persistence fails. JSON writers flush/fsync/close one exclusive .NAME.pending sibling before no-clobber hard-link publication; pending siblings remain evidence and are never canonical inputs, with no unlink dependency or added directory-fsync/crash guarantee. Report --sequence accepts the exact captured failure fallback, retains native causes/source/binding/pending facts and blocks; missing canonical sequence also yields blocked intent. THR259seq506 repair grants no new cleanup allocation.

- **Task-scratch reclamation contract (THR-195 B1).** `runtime/daemon/task_scratch_reclamation.py` accepts only immutable finalized ledger rows for valid canonical manifested `TASK-*` roots. Its caller-constructible `*Assertions` values are explicitly untrusted shapes, have no permissive defaults, and reject missing, malformed, stale-boot, truncated, ambiguous, unsupported-platform, recovery/job/live-reference, unavailable, or internally inconsistent values; B1 does not establish their provenance or independently validate lifecycle/liveness/current-boot authority, whose producers are deferred to B2/B3. The private `collect_revalidate_seal_consume_disposable` seam retains the typed values from each existing bounded E/C admission, compares liveness/session/process and complete coverage projections (including bucket classification/accounting and dominance) across E1..E4/C1..C3 while excluding collection timestamps, and binds final C3's canonical workspace/root/manifest/census fields to its successfully sealed stack-local row. It refuses malformed/private-identity/boot-mismatched or exhausted bounded observations before execution, and a partial executor failure claims zero while preserving the remainder for a fresh refusal. The sole production caller is the bounded pre-agent `run_step` hook. It is disabled by default through the strict boolean `workspace_cleanup.reclamation_actions_enabled` (default `false`), and disabling affects later admissions only — it cannot revoke an already admitted consumer call. It acts only on a third-or-later cleanup ordinal whose scheduler-created preclaim owner is assigned to a registered in-memory `TeamsRegistry` agent and reconciles to this invocation's initial successful `0 -> 1` claim (the first two runs stay report-only), applies one shared one-second deadline and at most 23 read/load admissions with no refill or recovery, and makes at most five best-effort calls to the unchanged consumer. Each attempt records the owner `workspace_cleanup_reclamation_attempt` audit before the prompt carries the known facts into the ordinary completion summary; the transported remainder is exactly the returned `after` accounting or `null`; scheduler triggering/order remains unchanged. It adds no writer fence or future-writer/same-UID guarantee. Sealing also re-derives the canonical root and applies the 60-second newest-mtime floor. Execution hard-skips Git/worktree/bare-repository ancestor or descendant evidence and cross-device ambiguity, uses fd-relative no-follow pathname removal with verified parent/root identity, exact allocated-byte/inode accounting, and complete protected manifest/lock/parent/directory-entry/sibling postconditions. Detected pre-action identity mismatches fail the row with zero reclaimed claims. Portable POSIX unlink/rmdir is not inode-bound: the threat contract deliberately excludes a hostile same-UID replacement in the final identity-check-to-pathname-syscall window, and does not promise that replacement survives. The separate report-only coordinator remains report-only after teardown and through the existing daily trigger; deployment, legacy-backlog eligibility, and activation beyond the explicitly enabled hook remain absent. Unmanifested `.happyranch/tmp`, `.tmp`, `.task-cache`, `.t`, shared `/tmp`, and pre-contract roots remain ineligible.

- **Report-only task-scratch evidence (THR-195 B2a).** `runtime/orchestrator/task_scratch_evidence.py` is consumed by the bounded report-only coordinator after task teardown and from the existing daily trigger. Task-scoped `task_scratch_report` audit observations record provenance, freshness, retention reasons and zero actual reclamation; no engine or deletion call follows. Its collector takes a finite, shared-deadline, fail-closed observation from full durable task/revisit/job/result records, advisory sessions, and Linux boot/PID root/cwd/open-fd reads; it checks admission before each bounded read, source open, and iterator advance, while a read already in progress remains nonpreemptible. Unavailable, partial, capped, stale, or identity-invalid measurements are not zeros; independently complete measurements (including zero) remain truthful when lifecycle, session, or durable authority alone rejects eligibility. Terminal rows, retained terminal results, and cleared trackers are not process-exit proof; B2b must recollect immediately before any action, and B3 remains coverage authority. It imports neither the engine nor `execute_ledger`. The separate database selection helper is read-only; its sole caller is the bounded pre-agent `run_step` hook, which supplies its initial `0 -> 1` claim context and per-read admissions. Each bounded SQL/decoding observation refuses as `None` when unavailable or malformed, and the helper still performs no config/consumer/audit work or grants action authority.

- **Report-only task-scratch coverage (THR-195 B3a).** `runtime/orchestrator/task_scratch_coverage.py` is consumed by the report-only coordinator with literal candidate identity bracketing, boot/time correlation and fresh recollection of any supplied coverage. It makes two bounded boot-bound observations of a fixed workspace partition, counts allocated bytes and entries (including hardlinks per observed entry), and describes dominant canonical or residual consumers. Every stat, open, dependent buffered read, directory read, and iterator advance is separately admitted against one deadline and finite shared bounds; manifest reads are limited by remaining actual-byte allowance and boot bytes do not consume that allowance. Literal no-follow validation rejects present symlink/non-directory optional, candidate-root, and manifest-parent paths, while absent optional parents are not invented as residual partitions. Incomplete candidate scans remain unknown and fail closed; observed repository/special entries remain disqualifying. Unavailable, changed, capped, zero, or partial observations are never ready. This is neither a permit, a free-space forecast, nor writer exclusion; B2b must recollect at action time.

- The temporary containment roots above are workspace-owned and measurable; they are not cleanup-eligible.

**Daemon-managed workspace cleanup scheduler (THR-195).** Cleanup is a **system-default daemon capability**, independent of all user Schedules (founder ruling THR-195 seq 129; per-agent defaults TASK-6036). `runtime/daemon/workspace_cleanup_scheduler.py` — a sixth daemon-owned loop registered in `runtime/daemon/app.py` — measures EACH AGENT's own workspace on a bounded, fail-open budget (one wall-clock deadline shared across every git subprocess via `min(cap, remaining)` + post-call expiry checks; workspace/repo/worktree caps propagate truncation). A bounded timeout, error, or cap/truncation result that makes measurement unavailable bypasses only numeric threshold evaluation, so otherwise-due spawning continues with honest unavailable advisory context; only an available numeric result below 1 GiB skips. Per agent, when the latest due daily local 03:30 occurrence (org-tz; existing occurrences delimit half-open windows compared as UTC instants, nonexistent spring-forward times are skipped and ambiguous fall-back times use `fold=0`) is unserviced — no marker at/after it, including legacy weekly rows — and no prior cleanup task of that agent anywhere in the complete history is non-terminal, the daemon triggers an ordinary root task ASSIGNED TO THAT OWNING AGENT with a daemon-composed brief carrying the fresh advisory snapshot — never through the repository-refresh prompt seam, never touching `schedules`. The first TWO triggered runs per agent are STRICTLY report-only; from run #3 the brief is the approved TASK-5552 §4 cleanup contract (bounded, Git-aware, non-force, action-time-re-derived eligibility; the advisory block itself never authorizes removal). Both the daemon daily marker and manual dispatch use the ONE shared `workspace-cleanup` TASK system contract (`requires_repo=false`); the exact manual first line is `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` and an unmarked manual request is inventory-only. Its bundled read-only `runtime/skills/bundled/workspace-cleanup/scripts/check_path_use.py` implements the approved THR-259 seq171/seq185 observation: an authoritative recorded terminal status plus a fresh complete same-user process scan replaces separate live-session/task-to-process identity; the fixed login/session daemons (sshd-session, systemd --user, (sd-pam), ssh-agent, gpg-agent, gcr-ssh-agent) qualify only by exact readable process name AND exact bounded cgroup role and are deliberately uninspected, while every other unreadable same-user process is unknown and skips. That is a snapshot with accepted later-opener/data-loss residual, never proof of OS-wide absence or executable identity. The shared procedure runs the scanner only through a task-bound host-visible HappyRanch job and accepts only an exact fresh matching receipt, with no direct fallback; candidate-specific task and trigger joins exhaust their keyset pages and fail closed on missing, changing, conflicting, malformed, or incomplete relevant evidence. A clean candidate may prove preservation through the accepted durable ref, an owning origin task branch whose head equals or descends from the candidate, an owning-task merged PR, or a containing any-task merged PR. The any-task route requires complete stable double-read discovery, merged/default-branch confirmation, and an independent complete stable compare; discovery alone and other-task unmerged PRs never count, while owning-branch unmerged evidence still refuses. Merged integration may not preserve original commit topology. Whole dirty worktrees remain protected; only a literal root `.venv` or `node_modules` cache may be removed after every cache gate and the 24-hour floor while tracked source bytes and Git status remain unchanged. Advisory content is aggregate-only and prominently ADVISORY / STALE ON ARRIVAL / not eligibility/candidate/safe-removal guidance; no path is enumerated; no pending jobs or `blocked_on_job_ids` are used as liveness. Routine scheduled and exact-marker manual cleanup reports remain durable on the existing agent page through normal task completion/results (THR-259 seq418). The scheduler creates no cleanup-report thread and workers have no routine thread posting or reuse obligation. Preserve all historical threads, messages, associations, results and audits; explicit founder-requested coordination, truthful anomalies, partial failures, unknown bytes, independent verification gaps and required current-session final report-completion remain. Kill switch: `workspace_cleanup.enabled` in the org `config.yaml` (default true). Docs: `docs/agent-guides/agent-executors-and-permissions.md`, `docs/agent-guides/orchestrator-contracts.md` §Daemon-managed workspace cleanup scheduler. First-two bookkeeping and the exact ordinal persist via existing durable mechanisms only (daemon-marked task rows via the complete read-only `Database.summarize_workspace_cleanup_marker_history` — bounded rowid keyset pages computing the exact all-history count, newest UTC instant and any-unfinished flag with no logical cutoff; lookup failures fail closed for triggering). After awaited measurement, task ID allocation, brief composition and ordinary task insertion run synchronously under existing `org.db_lock`, with no reporting-thread dependency; failed insertion is audited and never enqueued; workspaces are enumerated in bounded batches so no agent is starved; no schema/API/CLI/auth change. Disclosed unchanged limitations (daily cadence does not cure them): the selector's 1001-row history refusal and sixth-raw-terminal-row whole-batch refusal and the selector's bytewise-order refusal.

**Cleanup local evidence admission (THR307seq4).** The local evidence ordering is attempt envelope → fresh native gates → canonical
eligibility → first mutation → native terminal outcome → verified fsynced journal
append → canonical reference-only acknowledgement → whole-coverage admission.
The batch journal is the sole ordered outcome ledger. Direct Bash use needs the
same bound manifest/history/journal/evidence sink; stdout alone grants no action.
Cache isolation-directory creation is mutation. Failed rename/rmdir causes and
separate original/cache/directory identity and measurements survive; only positive
restoration/absence/source/status/raw-index proof admits safe refusal. Residue or
uncertainty halts. Git read observations suppress optional index refresh.
Each attempt and acknowledgement uses the existing exclusive pending write,
flush/fsync/close and exclusive hard-link publisher. A canonical file with its
identical expected staging sibling is valid; pending alone, conflict or collision
is not. Acknowledgements reference binding, envelope/eligibility identities and
exact journal prefix/row range/hash/ordinal, without a second success ledger.
Batch resume and report preflight the entire bounded namespace and journal before
any skip/admission; orphan or unacknowledged terminal evidence blocks, including
when fallback is missing/truncated. Explicit native failure/interruption wins over
readable files. Same-batch acknowledged prefix progress and later enclosing native
batch/sequence/postcheck completeness are separate prerequisites. No automatic
recovery or directory-fsync/crash-atomic guarantee follows. Historical bytes remain
historical, with missing new-contract proof explicit; never rebind or retry them.

Captured native uncertainty (output cap, timeout, spawn/capture failure, signal or
failed publication) is distinct from a fully captured ordinary nonzero gate
denial. The writer and every eligibility/coverage/resume/report reader retain
the native cause and block action or next admission, even with healthy enclosing
files or missing fallback. Native action/capture children inherit the candidate
cancellation group. The batch stops and reconciles its owned group before
terminal observations; outer capture allows that cleanup before forced kill.
Residual or unproven survival is unknown and unsafe, never clean completion or
permission to retry. Possible mutation remains partial/unknown without reuse.


**Owning-branch cleanup authority.** For daemon-managed workspace cleanup, an existing owning origin task branch is authoritative: if it does not contain the candidate commit, or its containment evidence fails, cleanup refuses without falling through to owning-task or any-task merged-PR proofs.

The current-use scanner treats a parseable leader `Z` as evidence for that task
only. It independently enumerates the bounded group frontier before requiring
leader namespaces or references. Repeated PID/TID, Tgid, starttime and complete
UID identity with repeated `Z` may omit only that task's dead references; live
workers still require their own `cwd`, `root`, `exe`, `maps`, private fd table,
mount/user namespaces and path resolution through their own proc root. Missing
worker census after leader exit remains `unknown`; `Threads: 1`, a controller's
TIDs or a guessed singleton never substitute for a complete census. Canonical
positive ASCII TIDs, uniqueness, actual group membership and unique complete
Pid/Tgid/Uid/State status fields are required. Malformed identities, reuse or
credential changes refuse; ordinary live R/S transitions alone are not reuse.
Thread membership/count observations converge within the existing pass cap
and shared deadline, reserving final census and identity/view checks. Known
positive use remains `blocked` even with incomplete evidence. Public result,
coverage and row schemas, scan/Bounds compatibility and exits 0/3/2 are retained.

For receipts from this revised source, `scanned` counts distinct admitted
non-exempt group leaders; `exited` counts distinct confirmed terminal task
identities (Tgid,TID,starttime,all four UIDs), with initially absent unbracketed
PID census slots accounted separately; `vanished` counts deduplicated actual
absence/closed-reference observations, including separately observed reopened
reference incarnations. A present confirmed Z contributes one exited and zero
vanished; leader/task-leader aliases and rediscovery do not recount it. These
counters are different units and must not be summed as population proof.
Historical receipts retain their original source-bound event meanings and
bytes. This correction grants no cleanup, package refresh, deployment or
live-process unreadability waiver.

**Forward-only root-terminal worktree reconciliation (THR-259 seq476 adopting474).** Each original newly committed structural root in `completed`, `failed`, `cancelled` or `superseded`, including NULL-parent workflow tasks, reconciles only its parent ancestry after existing history/teardown and outside owner locks. Recorded child `worktree-deferred:` evidence becomes advisory at this root pass without rewriting results/history. Child hooks retain their one-task/five-second deferral policy; manual/daily retain age/ordinal/peer/history/host-job/cache authority. The shared bundled safety owner proves fresh exact-HEAD preservation with live owning-branch precedence, recursive no-follow boundaries, unchanged same-user scanner and action-time registration/ownership/protected identities. Dirty/local-only/open/active/held/successor-used/unknown paths retain; terminality is not quiescence. Existing append-only audit start INTEGER id plus root id/created_at identify a pass; mutable timestamps, replay and missing finish never create a retry. One owner batch shares 10s work including 0.5s DB/50ms lock admissions,1s report,1s owned-group reap;16 events/256 nodes/depth64/page64/16 attempts/5s clipped candidates/200000 entries/1MiB capture/256KiB receipts. The 12s ordinary-I/O target cannot interrupt SQLite/fsync/kernel I/O. Incomplete membership/protection starts zero removal and names exact frontier/remainder; possible post-action or report anomaly halts. Success requires absence, unregistration, branch preservation and protected identities. No force, cache-only action, task-tmp action, new schedule/queue/retry, schema/auth/permission change, historical sweep or deployment follows. See [the full contract](../../agent-guides/orchestrator-contracts.md#forward-only-terminal-task-worktrees).

**Temporary-filesystem containment and inode advisory (THR-200/TASK-6501).** The workspace-cleanup advisory adds fail-open `statvfs` inode used/free/total/percent/threshold fields and a full-CI preflight. These observations are non-authoritative: they never grant cleanup authority, enumerate candidates, or cause the scheduler to inspect, move, quarantine, restore, unlink, or recursively delete `/tmp` content. Every runtime-launched task-agent and job subprocess now receives `TMPDIR`/`TMP`/`TEMP` rooted at the canonical mode-0700 `<workspace>/.happyranch/task-tmp/TASK-N`; runtime-owned, bounded, atomic v1 observations live separately at `.happyranch/task-scratch-manifests/TASK-N.json` and distinguish regenerable scratch roots from durable/recovery manifest evidence. Containment-variable or sidecar changes between preparation and launch fail closed; unrelated inherited environment is preserved. Manifests are report-only evidence and never deletion authority: automatic teardown, periodic eligibility expansion, backlog cleanup, and manifest-driven removal are not implemented. Executor language/package caches remain below `.happyranch/cache`, registered scratch worktrees remain below `.happyranch/scratch/worktrees`, and small daemon callback payload contracts are unchanged.

**Cleanup activity performance (issue1019 / THR-295).** The supported schema
path adds three nonunique indexes after legacy columns exist: tasks(assigned_agent,
created_at DESC,id DESC), audit_log(task_id,agent) where
action='workspace_cleanup_triggered', and task_results(task_id,agent,id DESC).
`CREATE INDEX IF NOT EXISTS` retains existing names without validating definitions.
The reader SQL is unchanged. Breaker delivery listing and mint use sequential
`await asyncio.to_thread` calls with the original DB RLock. Synchronized close
serializes connection access; removal can cause a logged sweep error. Cancellation
can leave a worker running or a committed pending token for later recovery.
There is no added orgs_lock hold, worker drain or lifespan guarantee.
