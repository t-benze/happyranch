# Darwin cleanup process-use observations — THR-346 S4

> Status: current
> Current source: bundled workspace-cleanup skill and jobs-and-cleanup guide
> Scope: S4 PR1 (S4a + S4b0 + S4b) and PR2 (S4c)

The target is the Mac app's daemon, with Linux parity. Source-native evidence is
an observation venue; frozen-app acceptance is outside S4 and remains NOT RUN.
Connector/launchd, daemon deployment, cleanup activation/defaults/schedules,
schema/auth/permissions, job configuration/bootstrap/host tools and dependencies
are outside this change. The accepted design artifact is
`dev_agent-2026-11-11-thr346-s4-design.md` (SHA256
`3c77d89a485646f4e74f6dc0d604727ed352d8ef5eca1c9594a2ada2fbbfd751`).

## Mechanical extraction

The exact S1 `_LibProc` implementation, exceptions and checked state move to the
single stdlib skill sibling `darwin_process.py`. Census re-exports preserve class,
exception, checked context and thread-local identity. The S1 BSD-info layout,
PID/PPID self-check, start string, sysctl identity fallback and permissive sampling
semantics remain unchanged. No second libproc loader or historical truth-probe
BSD decoder is added. Installed scripts resolve a fixed sibling without runtime
imports; the exact package evidence list and whole bundled-tree packaging include
it. External PATH python3 remains an existing installed-script prerequisite.

## Portable helpers

Runner own-directory resolution uses strict stdlib Path resolution from
BASH_SOURCE. Both invalid raw regex escapes are fixed. Both embedded parsers and
the snapshot parser require nonempty parseable absolute BSD mount tables including
root. Invalid evidence retains or follows the existing mutation-aware restoration
handling. Linux mountinfo parsing/output and the Linux scanner validator remain
unchanged. S3's frozen helper argv repair is already shipped and is not repeated.

## Darwin observation contract

Strict cleanup reads return typed success/gone/denied/error/short/capped outcomes
with native byte counts and errno. Native structs follow [Apple's fixed XNU
proc_info.h](https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.101.15/bsd/sys/proc_info.h).
LP64 sizes/offsets and real own cwd/fd path+inode decode must validate before
negative observations. Reads cover process cwd/root, vnode fds, executable path,
thread-local cwd and public region paths. Null optional root/thread vnodes differ
from short or malformed reads. Region traversal requires progression and bounded
addresses/bytes/entries; initial unexplained EINVAL and submaps retain.

Boot identity comes from `kern.bootsessionuuid`. Every population binds PID to
S1's `sec.usec` start identity and complete real/effective/saved UID/GID, flags and
exec names; identity bookends and repeated census detect churn/reuse. Only stable
known uniform root/other-user credentials exclude a worktree process from scope.
Unreadable identities, mixed credentials and denied same-user processes retain.
Darwin has no cgroup/name/launchd exemption. Scratch's all-PID scope is unchanged
and is not routed here in PR1. No privilege or access change follows.

Matching uses canonical contained paths with observed vnode identity and a bounded
literal metadata inventory, including inode hits after hardlink/rename. Symlinks,
mount boundaries, lost path identity and inventory drift retain. All admissions
spend one caller deadline and aggregate cap: at most4096 PIDs,256 threads,4096 fds,
6 census passes,4MiB mapping evidence/20000 region or metadata entries,30s default;
smaller caller budgets remain. Already admitted kernel calls are not preemptible.

Positive in-scope use produces BLOCKED/exit3 even with other gaps. Negative
Darwin observations currently produce UNKNOWN/exit2: public region vnode absence
cannot prove complete negative mapping coverage or fence mapping churn. The
explicit reason is `public_region_negative_coverage_unestablished`; no zero maps
or idle clearance is manufactured. Future complete negative support needs real
coverage evidence; this is not a future-opener fence or hostile same-UID defense.

Darwin JSON has an explicit coverage platform, boot UUID, native ABI/population
and lane evidence. The shell validates a closed Darwin schema only after the
unchanged native job/session/nonce/script/cwd/freshness/output checks. Missing,
malformed, blocked or unknown output retains. Linux schema and branch remain.
The observer records real BSD `/sbin/mount` stdout/stderr/exit and boot identity,
without fake procfs, namespaces, init-name or findmnt fields. Longest covering
mount metadata and package/protected evidence stay required.

## Evidence and limits

One suite module, `suite_cleanup_process_use.py`, extends the existing native
probe through registration and one lazy dispatch. Portable rows execute shipping
mount functions and test-side syscall fixtures; native rows use owned child
cwd/fd/mmap/thread ground truth and the shipping scanner CLI. Real kernel EPERM is
separately labeled or NOT OBSERVED. Read-only observer before, safety snapshot and
preservation gates are exercised. The offline fixture cannot prove remote Git
preservation or authenticated live-task/job acceptance and records those refusals.

STOP-P accepts these gate-specific previews: idle eligible at the candidate-runner
boundary is unproven. No new preview mode is added; neither candidate runner nor
observer run is a dry-run. STOP-S preserves all-PID scratch and EPERM retention.
STOP-C preserves complete negative coverage requirements and UNKNOWN. STOP-F
keeps frozen acceptance NOT RUN. STOP-I preserves the external Python residual.

Suite time is240s with10s reserved for owned-child release/TERM/KILL/reap/absence
and fixture disposal,64KiB command stream and32KiB report limits. Missing tools,
imports, native observations or cleanup remain visible. Exactly18 locked wheels
in an isolated --without-pip venv are a pre-submit closure gate; no new dependency,
job or bootstrap change follows. PR1 used1/3 (build14); PR2 is execution2 and
execution3 remains held for a diagnosed failure. This source change submits no
build. Review, scoped QA, exact-head lint and guarded merge remain.
Python units are retired; general integration is SKIPPED THR-243 seq42. No ad hoc
Product E2E. Static/source/simulated evidence never becomes native/frozen proof.

The build15 evidence-loss fix keeps all named rows and independent facts. Only
this source suite's dispatcher output uses compact JSON, with the same keys and
values; suite registration and job/bootstrap caps are unchanged. Size includes
final completion/error fields and newline. Worktree hit diagnostics omit repeated
paths, keep at most four PID/kind/device-inode hits, two reasons and128 stderr
characters. Scratch/real-errno reasons keep at most four distinct192-character
entries, with explicit middle elision retaining PID/errno ends. Above28KiB the
suite first removes optional worktree hits/stderr/additional reasons and protected
errors and records them in `optional_detail_omitted`; worktree state, exit, lanes
and the first reason remain. It retains every row's name/status/expected/
actual/provenance, source SHA/hashes, owned PID/start/cwd/device-inode ground truth,
boot UUID, ABI, scratch decision/count/coverage/boot facts and child/fixture cleanup.
`row_status` is validated even after native/cleanup failure; failure text includes
observed/failed counts. If mandatory evidence alone still exceeds32KiB, the suite
fails with `report_capped=true`/`evidence=INCOMPLETE` and retains the oversized
evidence instead of silently discarding rows. This negative is not within-cap
acceptance. Build15 remains a failed native run with all92 rows UNOBSERVED.
Four new portable `report_*` rows cover96-row realistic worst-case encoding
(256-character paths, 64-bit identities, full diagnostic list bounds), optional
overflow, irreducible mandatory overflow and early failure. The realistic case
must fit below28KiB with all rows/facts intact. These fixtures do not replace
native review/QA or spend the manager's reserved execution3 (2of3 already used).

## S4c scratch and existing consumers

The same suite now includes scratch evidence, coverage and report observations.
The shared leaf's explicit `all_pids=True` mode is used only by scratch; its
same-user worktree default and receipt shape remain unchanged. Every PID's
strict native identity/reference reads use the caller's remaining five-second
budget, with 10,000 PID/fd limits and no nested deadline refill. Kernel
`kern.bootsessionuuid` brackets collection; final PID/start comparisons use the
same shared reader. EPERM/EACCES and incomplete lanes produce null counts and
retain. Public negative region coverage remains unestablished, so even a readable
idle host cannot currently establish global scratch clearance. Known cwd/fd use
is retained in reasons despite incomplete measurement. No UID filter, exemption,
privilege or new loader is introduced.

Coverage supports Darwin boot identity and the existing workspace partition,
keeping every no-follow/device/manifest/read/byte/entry/deadline/coherence gate.
Default `/proc` arguments select the native host route on Darwin; explicit
nondefault procfs fixtures retain Linux behavior and API/output shapes. The
coordinator therefore needs no new platform route or report schema. Daemon
compatibility aliases retain module/type identity. Unsupported platforms refuse
the reclamation consumer before collectors. Its former dormant/test-only prose
is corrected: run_step's pre-agent caller is real, conditional and disabled by
default; ordinal/owner/claim and stack-local ledger binding are unchanged.

Native `scratch_cwd_retain`, `scratch_fd_retain`, `scratch_idle_or_denied` and
`native_root_unknown_retains` extend all PR1 rows. Synthetic all-PID/different-UID,
EPERM, boot-mismatch, coverage-change, final identity/deadline and Linux fixture
rows run through the real collectors/report/audit APIs on disposable state.
The root row supplies actual shared scanner UNKNOWN to the real root consumer,
isolating prior owner/Git facts at a recorded command boundary with a removal
sentinel. It never runs a candidate runner or enables reclamation. Real kernel
EPERM evidence is separate from simulated errno and may be NOT OBSERVED.
Frozen-app acceptance remains NOT RUN. Neither cleanup_diagnostics nor nightly
disk-sweeper process policy changes here.
