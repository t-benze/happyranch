# Darwin cleanup process-use observations — THR-346 S4

> Status: current
> Current source: bundled workspace-cleanup skill and jobs-and-cleanup guide
> Scope: S4 PR1 (S4a + S4b0 + S4b); S4c follows separately

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
job or bootstrap change follows. No native build is submitted by the maker; PR1
uses execution1, PR2 execution2, execution3 stays reserved, budget0/3 until actual
manager submission. Review, scoped QA, exact-head lint and guarded merge remain.
Python units are retired; general integration is SKIPPED THR-243 seq42. No ad hoc
Product E2E. Static/source/simulated evidence never becomes native/frozen proof.

S4c will extend this same suite with scratch evidence/coverage/report and existing
root consumer routing, preserving all-PID scope and existing budgets. Neither
cleanup_diagnostics nor nightly disk-sweeper process policy changes here.
