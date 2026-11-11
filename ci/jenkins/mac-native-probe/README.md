# Manual Mac native probe

`happyranch-mac-native-probe` is the **one** new job authorized by Founder
THR-346 msg6 (root TASK-15047, S0 TASK-15050). It observes Darwin prerequisites
for the frozen Mac app. S0 does not build the app, run a daemon, fix runtime
code, install tools, or change the host. Connector/launchd work is excluded.

The job already exists. Founder THR-346 msg20 authorizes updating that **same**
job only from a reviewed and merged `config.xml.in`. The engineering manager or
founder applies the merged file through the existing job's configuration API;
this PR does not update the live job. Never create or copy another job, and do
not trigger a build under this repair's authority. Record the merged source SHA
and configuration SHA256 with the update receipt. This is a
Freestyle job with NullSCM: neither controller Git nor the Git SCM plugin is
used. The existing `mac-mini` label and build-timeout plugin must be available;
do not install or reconfigure them on this authorization.

The authorized controller API origin is `http://127.0.0.1:8081`; use
`curl --netrc-file /home/benze/.config/jenkins/netrc` with the bounded curl
wrapper in KB `jenkins-job-setup-and-durable-observation`. The browser-only
address is `http://192.168.18.174:8081`. Use only this credential-file reference;
never read, print, copy or embed its contents or other
credentials in the template, source checkout, parameters or archived evidence.
Missing credentials block configuration updates/submission. No controller
executors, sudo, credential configuration, daemon deployment or restart is needed.

Submit manually with:

- `SOURCE_SHA`: required 40-hex commit. The shell validates it, initializes a
  fresh empty checkout, fetches **only that SHA** with `--depth 1 --no-tags`,
  checks out detached, and checks `HEAD` against the requested SHA. Global Git
  configuration and credential helpers are disabled for this public fetch.
- `SUITE`: defaults to `truth`. The shell accepts only a bounded identifier;
  the pinned Python dispatcher's argparse allowlist validates the actual suite
  before spawning anything. This is deliberately a string parameter so future
  reviewed slice PRs can extend the dispatcher without editing the Jenkins job.

There are no SCM/cron/remote-token/upstream/downstream triggers, concurrent
builds, controller checkout or credential bindings. The absolute build timeout
is 30 minutes. The EXIT trap removes only the build's fresh checkout scratch;
`evidence/` stays for archival. Catchable cancellation runs cleanup; SIGKILL,
agent loss or machine failure may leave scratch and must be reported honestly.
The next run does not sweep previous scratch. Jenkins retains 20 builds/30 days.

## Output bounds and failure handling

`build.log` and `probe.log` each retain at most 4 MiB (4,194,304 bytes). A pipe
reader writes only that many bytes and checks one extra byte: exceeding the
cap fails the build nonzero and closes the pipe. This also bounds checkout
command output without imposing a file-size limit on Git's fetched packfiles.
The dependency bootstrap and Python probe share `ulimit -f 32768`: each regular
file, including `probe.json`, is limited to 32 MiB (33,554,432 bytes). This job
runs `#!/bin/bash -p`, not POSIX mode, so `-f` uses 1024-byte units. Apple's
[Bash 3.2 implementation](https://github.com/apple-oss-distributions/bash/blob/main/bash-3.2/builtins/ulimit.def)
sets the file-size block factor to 1024; a local Bash 5.3.9 `ulimit -f 1`
probe reported `RLIMIT_FSIZE=(1024,1024)`. Native confirmation remains separate.
A file-size-limit signal or write error fails nonzero. The 4 MiB log pipe caps
remain independent of this per-file allowance.

`identity.txt` is capped at 64 KiB (65,536 bytes), including cleanup. Its producer
uses the same pipe reader with a 65,514-byte cap, reserving 22 bytes for the
longest `scratch_removed` receipt. Identity overflow or a write error fails
nonzero. The receipt append is checked; a failed append cannot claim success.

The parent shell owns scratch and runs cleanup after the capture pipeline ends.
It saves the original stdout/stderr before capture and copies only the first
1 MiB (1,048,576 bytes) of `build.log` to the saved console descriptor, followed
by a truncation marker when needed. All console output, including cleanup and
saved-descriptor diagnostics, passes through one additional 1 MiB cap plus a
fixed truncation marker. Excess console input is drained without retaining it,
so overflow cannot interrupt cleanup with SIGPIPE. The parent waits for this
reader and checks its status. Cleanup never writes the log back into itself.
The reader is an ordinary child connected through `mkfifo`'s private
`evidence/.console-pipe`, compatible with Bash 3.2; cleanup checks its unlink.
Bootstrap errors before this reader starts suppress diagnostics and exit nonzero.
Log, identity or console overflow fails nonzero. Cleanup attempts to append
`scratch_removed=true` or `false`, including for an early platform/parameter
failure; a failed receipt may be absent. Removal, receipt append, size read,
console copy, marker write or console-reader failure turns an otherwise
successful run into a failure without replacing an existing nonzero exit code.
Catchable interruption retains the existing INT=130 and TERM/HUP=143 statuses.

## Private dependency bootstrap

After immutable checkout and identity capture, every suite uses a private venv
created with `python3 -I -B -m venv --without-pip`. The job requires regular
CPython 3.14 on macOS arm64; it refuses other interpreters and free-threaded
builds. A stdlib-only installer reads the **checked-out** `uv.lock`. Its finite
package set is the 18-package import closure measured for `cleanup-helpers`:
annotated-doc, annotated-types, anyio, click, fastapi, pydantic, pydantic-core,
pydantic-settings, python-dateutil, python-dotenv, python-multipart, pyyaml, six,
sse-starlette, starlette, typing-extensions, typing-inspection, uvicorn. This is
an import set, not a resolved runtime environment: it supplies no optional
extras, command entry points or unimported metadata dependencies. A future suite
with new imports needs a reviewed set update. The pinned dispatcher still owns
suite validation; the bootstrap does not add suites.

Versions, artifact URLs, SHA256 hashes and exact byte sizes come solely from
that checkout's lock. Exactly one locked package per name is required. Selection
admits `cp314-cp314`, compatible older `cp3*-abi3` macOS arm64/universal2 wheels
whose deployment target fits the host, or `py3-none-any` wheels. No sdist, index
lookup, dependency resolution, pip, uv or ensurepip is used. Direct HTTPS to
`files.pythonhosted.org` is required; ambient proxies and redirects are refused.
Every download must match the locked size and SHA256 before ZIP contents are used.

Limits are 16 MiB per wheel, 64 MiB total downloads, 128 MiB total extracted
payload, 32 MiB per extracted file and 20,000 ZIP members total. A 300-second
alarm/deadline covers selection, venv creation, download, validation and
extraction inside the unchanged 30-minute job timeout; network reads also have
at most 20 seconds each. Missing/incompatible artifacts, network errors, hash,
size or cap violations, unsafe ZIP names (absolute/`..`/ambiguous paths), links,
special files, duplicate destinations and unsupported wheel data schemes fail
closed with a diagnostic and nonzero exit. Only ordinary files/directories are
accepted; `.data/purelib` and `.data/platlib` relocate into the private site.

All venv/wheel/temp files live under the existing build scratch. `HOME`,
`XDG_CACHE_HOME`, `TMPDIR`, `PYTHONUSERBASE` and pip cache paths point there;
`PYTHONNOUSERSITE=1`, bytecode writes are disabled and inherited `PYTHONPATH` /
`PYTHONHOME` are unset. No host, Homebrew, user-site or original `~/.cache`
installation occurs. Verified archives are removed after extraction. Bootstrap
output uses the existing capped probe/build/console streams; the existing EXIT
trap removes scratch, including partial bootstrap files, and records its receipt.
The live job must be updated only after independent review, QA and merge.

## Probe contract

From a checkout, using an existing Python 3.12–3.14 interpreter:

```sh
python3 scripts/mac_native/probe.py --suite truth --output /absolute/probe.json
```

Suites: `truth` (S0), `teardown` (S1 lifecycle), and `disk-sweep` (S2 cleanup).
`cleanup-helpers` covers S3. `supervisor` covers S6: four read-only toolchain
discovery rows precede scratch-contained focused Swift tests and a scripted
Stop escalation against a disposable child. It requires the job's existing
scratch TMPDIR; unavailable Swift >=6/developer directory/macOS SDK exits
nonzero without building. Commands share a 1200-second suite deadline, retain
at most 1 MiB of command output in memory and report only 4 KiB tails, within
the existing log/report/file caps. It installs no tools and changes no job
configuration. See [the app behavior and test scope](../../../app/mac/README.md#managed-daemon-stop-and-health).
The existing job accepts these suites without configuration changes.
JSON goes to stdout and the optional output path;
human observations go to stderr. Linux prints `darwin-only`, emits skipped
JSON and exits 0. Darwin findings, including a broken repo decoder or unavailable
mount API, are data. Internal exceptions, incomplete process-tree cleanup and
output errors exit nonzero. The original suites import stdlib only; the job
now supplies the private dependency set above for later authorized suites.

The probe imports the pinned checkout's `_LibProc._ProcBsdinfo` when possible;
otherwise it explicitly labels a byte-identical copy of the S0 base decoder
(`9ed24be93`). That fallback is historical, not an assertion about a future
changed checkout. The independent corrected layout includes `pbi_xstatus`
after `pbi_status`, following Apple's
[`proc_info.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h).
The 64-bit mount API layout follows Apple's
[`mount.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/mount.h).
Both decoder sizes and kernel byte counts are recorded. Each process reports
its own PID/PPID/PGID using `os`; child creation windows and a native `ps`
reference supply separate start-time/state evidence. Status is a momentary
sample, so two successive calls may legitimately differ. The probe's parent
spawns a child with `start_new_session=True`, and that child spawns and reaps a
grandchild. Cleanup sends TERM to the owned group, waits for the child to reap,
escalates only if needed, and checks both PID absence and group absence.

Other observations include `O_PATH`/no-follow flags, `/proc`/mountinfo presence,
raw `mount` samples, ctypes getmntinfo/statfs feasibility and mount points,
`df -Pi`/`df -i` header plus one row, and a fresh `$TMPDIR` case-collision trial.
The latter describes that temporary volume only; it must not be generalized
to all APFS volumes. macOS/architecture/Python/UID/RLIMIT_NOFILE facts accompany
the observations. Probe cleanup operates only on its own tree/temp directory.

Archive and collect `evidence/probe.json`, `probe.log`, `build.log` and
`identity.txt`. Record request → queue ID → exact build number/URL/result,
source SHA, suite, node, Python identity and scratch cleanup. SUCCESS alone
does not prove the observations or cleanup: inspect the JSON and identity.
S0 allocates one build; environmental correction permits at most three total.
A lost POST response never authorizes blind resubmission. Inspect the original
queue/build; do not use `lastBuild` as identity. Use a durable HappyRanch job for
a long wait/collection and report in-flight state rather than holding a session.
This job is separate from lint/static-only required PR checks and Product E2E;
no GitHub workflow or CI trigger is added.

## S1 teardown suite

```sh
python3 scripts/mac_native/probe.py --suite teardown --output /absolute/probe.json
```

On Darwin this imports the pinned product factory and macOS backend, records the
selected backend, launches a real parent → child → grandchild session group
through `prepare`/`launch`, samples RSS/CPU/process count and provenance once,
and calls normal `finish`. It records the complete receipt, clean/quiescent and
survivor fields, plus signals derived from the receipt's cleanup status (not
independent signal-delivery proof). Every spawned PID must independently return
ESRCH to `os.kill(pid, 0)` after product finish and before probe cleanup. JSON
checks carry individual PASS/FAIL; failed assertions exit nonzero. A separate
interpreter forces libproc loading to fail and verifies successful factory import
and passthrough selection using the product `PassthroughBackend` class and
`CapabilityReport.backend` returned by `probe()` (the fallback has no `name`).
The same teardown run compares the product's full BSD PID/PPID/PGID decode for
self and all three workers to their independently published `os.getpid()`,
`os.getppid()` and `os.getpgid()` values. It also masks only task counters on
real product observations to check P07 missing counters versus observed zero
through both the backend and descendant sampler, restoring the reader afterward.

Checked identity failures retain PID, returned/expected byte counts and numeric
errno/name in the product exception. A probe-only wrapper retains the first
eight failures, tagged launch/sample/finish; at most four distinct PIDs get a
later `/bin/ps` numeric PID/PPID/PGID/UID/RUID observation plus bounded
`getpwuid` name. Known worker membership is recorded separately; other rows
remain unknown descendants. These later diagnostics do not authorize skipping
any row. Build #3 at `4fa5e4b78f79b723893e3d82ea442fd57088309b` observed EPERM
for one unrelated PID; other foreign-UID rows were readable. It is not a UID
exclusion rule. Product EPERM reads now try identity-only
`CTL_KERN/KERN_PROC/KERN_PROC_PID` sysctl, validating the LP64 648-byte
`kinfo_proc`, requested PID and start time. ESRCH/successful zero-length means
gone; both reads failing or malformed fallback remains unknown and sticky.
Fallback rows participate in normal lineage/group membership, with unavailable
RSS/CPU (no invented counters or nfiles); a missing member counter makes that
tree aggregate unavailable, while fully observed zero remains zero.

The teardown suite additionally cross-checks sysctl PID/PPID/PGID/start identity
against independent libproc decoding for self/parent/child/grandchild. Its
`sysctl_fallback` records distinct host PID count (at most4096), read count and
numeric triggering errno counts, with no argv/env or PID list. This is observation
of successful fallback use, not a forced-permission test or frozen-app proof.
The ABI follows Apple's pinned
[`proc.h`](https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.101.15/bsd/sys/proc.h),
[`sysctl.h`](https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.101.15/bsd/sys/sysctl.h)
and public `vm.h`/type headers. Product initialization of the fallback also
cross-checks its own identity against libproc and OS PID/PPID/PGID.

Sample failure still attempts
normal product finish, recording its refusal and absence of a receipt honestly.
Finish is unchanged: when the census remains uncertain it refuses before group
termination and its direct-child wait can time out. The spec's current macOS
exception contract conflicts with its broad terminal-teardown/INCOMPLETE wording.
Returning a best-effort INCOMPLETE receipt alone would not preserve the current
supervisor admission gate for finish-only uncertainty. The manager must resolve
that contract/scope question before a separate lifecycle change.

Probe-owned TERM/KILL disposal runs before optional `ps`/account enrichment,
whose per-command two-second timeout and four-PID cap remain. Each worker records
its native PID/start-time tuple before spawning descendants. Rescue authenticates
that tuple afresh before each signal; an unreaped direct-child handle also covers
the root during partial readiness. ESRCH or reaping permanently retires a worker,
so reuse of its numeric PID cannot reactivate it. Unreadable/mismatched identities
are permanently excluded from signalling and reported as unknown residue, never
clean. Rescue uses individual identities rather than a saved process-group number.
Signals, retired workers and final original-worker absence are recorded separately;
a cleanup rescue never changes a product FAIL to PASS. These observations do not
provide an atomic kernel identity-and-signal operation.
Linux emits the same skipped JSON shape as `truth` and exits 0.

Product imports use stdlib only. `truth` observations retain their S0 meaning.
Native evidence at the repaired head: NOT RUN. Prior build #3 failed as described
above; Linux skipped output and lint are not native proof.
No Jenkins creation/reconfiguration, additional trigger, app build,
deployment or daemon restart is part of this slice.

## S2 disk-sweep suite

```sh
python3 scripts/mac_native/probe.py --suite disk-sweep --output /absolute/probe.json
```

The hyphenated name fits the existing shell identifier rule; only the pinned
Python SUITES allowlist/dispatcher changes. No Jenkins configuration change,
new job or trigger is needed. Native execution belongs to the program manager;
authorship and Linux skipped JSON do not spend a native build or prove Darwin.

The suite reads the real product Darwin mount set and checks unavailable, bad-layout
and malformed evidence retention, then calls real `sweep_org`
in dry-run mode over a private synthetic org with bounded SQLite ownership,
manifest and terminal-lineage evidence. A synthetic age clock admits TASK-1 and
retains recent TASK-2. Complete before/after snapshots must match: preview never
exercises unlink and cannot prove mutation. A separate private tree invokes real
`mutate(remove=True, scratch=True)` on a file/internal hardlink pair, symlink,
Unix socket and FIFO. A test-side guard rejects every leaf open; observed unlink
names and root removal prove all five leaves were removed. An outside symlink
target must survive. Four hardlink sibling drift cases (mode/size/mtime/nlink)
and six final-stat drift cases (swap/mode/size/mtime/nlink/ctime) require retention
with no unapproved unlink. Changes are injected by test-side syscall wrappers;
there is no production test-only seam.

All fixtures live in a private temporary directory inside the job checkout,
removed by its context manager; successful JSON additionally asserts removal.
Each fixture payload/manifest is <=4096 bytes; SQLite uses 4096-byte pages with
max_page_count=16 and no journal; the fixed table/row set cannot grow beyond
64KiB. The suite has a shared 60-second cooperative product deadline, bounded
case/diagnostic counts and a 64KiB serialized report cap before stdout/file
writes. Existing job per-file/log/timeout limits still apply. Linux emits skipped
JSON and exits0. This source probe does not establish frozen helper dispatch
(S3), workspace-cleanup scanning (S4), native parity until executed, or physical
space reclamation. Python units remain retired, integration skipped THR243seq42.

## S3 cleanup-helper probe

`python3 scripts/mac_native/probe.py --suite cleanup-helpers --output /owned/probe.json`
selects the reviewed S3 suite through the existing job's `SUITE=cleanup-helpers`
parameter. No job configuration or trigger changes are needed. Default execution
uses source modules and a private executable adapter with simulated `sys.frozen`:
**it is NOT frozen-app proof**. It checks all three helper parsers, closed-name
refusal, unchanged source argv, loader-only revision identity/change/unavailability,
a real supervised nonmutating dry-run worker over a private synthetic org, and
the real run-step command boundary retaining unknown scanner output. Git/owner
facts at that last boundary are test-side stubs; no actual worktree removal or
Darwin process-use clearance is claimed. Thirteen named checks share a 55-second
cooperative budget; child stdout is drained with a 64KiB cap per command,
fixtures are <=4096 bytes, SQLite is <=16 pages of 4096 bytes, and scratch is removed.
The default adapter advances only its test-side wall clock by two hours so the
private candidate is eligible; the unmodified product must still preview it
without removal. A supplied binary sees a recent fixture and must retain it.
The existing job still imposes the absolute 30-minute and file/log limits.

For a separately built, provenance-verified bundle, the same suite accepts
`--frozen-daemon /owned/happyranch-daemon/happyranch-daemon`. It then executes that
binary's helper parsers and supervised worker. The parent revision checks still
run in source Python; this is **not** proof of the frozen parent's own revision
or a complete frozen manual-preview invocation. Those require separately owned
bundle build/run evidence. Source runtime dependencies must already be available
to the job's Python; missing dependencies are an environment failure, never PASS.
The suite never builds a bundle or installs tools.

The existing Jenkins job cannot build the frozen bundle inside its probe-only
`ulimit -f4096`: each regular file is capped at 2-4MiB, below fresh CPython native
libraries/stdlib archives and PyInstaller bundle outputs. The documented freeze
requires a fresh managed CPython 3.12.10 on Darwin arm64; uv/toolchain availability
on the mini is unverified. Full packaging additionally requires Xcode 16, Node 24,
Swift/Go and allows 45 minutes for build in a 95-minute workflow, versus this job's
30-minute absolute timeout. These are blockers, not permission to change the
job, add another job/trigger or launch the packaging workflow. Founder owns any
job-config/venue decision; the manager owns all S3 native runs (0 of 3 spent by
this maker). Native Mac and frozen Mac bundle evidence remain NOT RUN here.

On Linux, the normal dispatcher emits skipped JSON. For the explicitly allowed
source/simulated-frozen substitute, call the existing suite function directly:

```bash
python - <<'PYPROBE'
import importlib.util
spec = importlib.util.spec_from_file_location('mac_probe', 'scripts/mac_native/probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
report = {}
probe.cleanup_helpers(report)
print(report)
PYPROBE
```

Run that command under an external <=60-second timeout and output/file caps;
record it as Linux source/simulated-frozen logic only. Do not use Linux results
as native or frozen proof. Python units remain retired; integration stays skipped.


## S7 case-safety suite

```sh
python3 -B scripts/mac_native/probe.py --suite case-safety --output /owned/evidence/probe.json
```

The existing string `SUITE=case-safety` selects the new module through the pinned
dispatcher. No job configuration, dependency, tool or cap change is needed.
Manager-owned native execution is a later leg; this PR consumes no native build.
Linux emits the standard `darwin-only` skip and exit0.

On Darwin, `case_insensitive_apfs_fixture` uses the existing statfs layout and a
real alias lookup to require APFS with case-insensitive behavior in the build's
checkout scratch. Wrong filesystem/case mode fails rather than claiming proof.
Named expected/actual rows cover `artifact_leaf_alias`, `artifact_folder_alias`,
`artifact_nested_leaf_alias`, `artifact_exact_overwrite`, `artifact_route_400`
(including no audit on refusal), `custom_skill_refusal_preserves_alias`,
`artifact_alias_before_replace`, `artifact_no_temporary_residue`,
`manage_repo_early_validation` (HTTP422, unchanged canonical bytes, no writer, git
or bootstrap), `context_builder_early_validation`, `context_builder_lowercase_pull`,
and nine `repo_parser_*` grammar rows. `private_scratch_removed` records removal.

The real store, frontmatter parser and route handlers run in-process with private
files. Only git/network, workspace bootstrap, post-success audit and native-writer
bookkeeping are isolated; this is not live route/auth or frozen-app acceptance.
The suite uses only stdlib plus imports from the existing 18-package set.
Fixtures are fixed-count and each payload is below 4096 bytes; no database is
created. Its complete report is checked below 60000 bytes before stdout/output.
There are no external commands or waits in the suite. All fixture writes are
inside one temporary directory under the checked-out source in build scratch,
removed before success. Existing 30-minute, 32 MiB file, 4 MiB log and 64 KiB identity/
probe limits remain. Namespace checks do not claim to serialize racing external
renames or simultaneous differently-spelled writers.

## S5 repository refresh probe

`python3 -B scripts/mac_native/probe.py --suite repo-refresh --output /owned/probe.json`
selects `SUITE=repo-refresh`. Linux emits skipped JSON and exits0. Darwin uses
the real macOS process-group backend, HostSessionSupervisor, native producer
reservation/audit preparation object and shipping refresh/completion path.
A private bare origin and writer advance one commit beyond a tracked clone;
named expected/actual rows require a healthy BEST_EFFORT probe, stale clone,
actual Git admission, true refresh, exact new HEAD, Boolean-quiescent receipts,
zero reported survivors and closed preparation. A real passthrough negative
must leave the clone stale without opening a repository obligation.

The suite uses only stdlib and the existing locked 18 bootstrap wheels, local
Git, a private HOME/config and `$TMPDIR` scratch. It performs no network/daemon/
frozen-app operation. Fixture Git commands have five-second deadlines and
file-backed output capped at 4096 B; each product batch retains its original 30s budget.
The suite has a 120s alarm and a 16KiB report cap; ordinary/catchable completion
removes scratch. The existing job owns outer interruption cleanup. No job
configuration, dependency or trigger changes; native Jenkins and frozen-app
acceptance are separate authorized legs. Quiescence remains BEST_EFFORT: a
descendant escaping both group and identity-safe census may go undetected.

## S9 inode telemetry probe

`python3 -B scripts/mac_native/probe.py --suite inode-telemetry --output /owned/probe.json`
registers `SUITE=inode-telemetry` in the existing dispatcher. It needs only
stdlib Python, `/bin/bash`, `df`, `awk`, `uname` and `tee`, all already present
on macOS. No dependency, job configuration, host or trigger change is needed.
Only a separately authorized native leg operates the Jenkins job.

The `inode-telemetry` suite runs all 12 rows: nine portable fixtures followed
by three real-df checks. `suite_inode_telemetry.py` extracts the shipping
`observe_tmp_inodes` function without sourcing/running `local_ci.sh`. The
`inode_telemetry_fixtures` report section contains `gnu_low`, `gnu_warning`,
`bsd_blocks_high_inodes_low`, `bsd_inodes_high_blocks_low`, `bsd_large_total`,
`gnu_malformed`, `bsd_malformed`, `df_unavailable` and `uname_unavailable`.
Apple `/bin/bash` then executes that function against real `/bin/df -Pi`
for a private directory inside the job's `$TMPDIR`; test-side `tee` saves the
same sample for independent column
comparison. Named rows carry expected/actual values: `real_df_inode_values`
compares used/free/total/percent with BSD inode columns 6–8 and their sum;
`real_df_advisory_exit` expects 0; `real_df_warning_threshold` checks the 90%
warning against the actual inode percentage. The report retains the df sample,
advisory text and Bash version. A failed row exits nonzero.

Each Bash command has a five-second deadline, incrementally drained output
capped at 16KiB and kill/reap on failure. Snapshot reads are capped at 16KiB;
the successful suite report is capped at 32KiB before the dispatcher's formatted
JSON (below 64KiB). Scratch is removed on ordinary/catchable completion; the
existing job owns outer interruption cleanup and all file/log/time limits.
No whole CI, build, daemon, external HOME/cache write or frozen bundle is used.
On Linux the dispatcher emits its expected skipped JSON and exits0.

The same nine portable fixtures can also run on their own with
`python3 -B scripts/mac_native/suite_inode_telemetry.py` (see `docs/local-ci.md`).
They prove parsing/advisory behavior only; native Mac, frozen-app acceptance
and Product E2E require their separately owned evidence.


## S4 cleanup-process-use suite

`scripts/mac_native/suite_cleanup_process_use.py` owns the one extensible S4
suite, registered as `cleanup-process-use`. PR2 adds scratch routing to this same
module. It needs only stdlib in addition to the unchanged locked 18-wheel job
bootstrap; no job parameters, dependencies, host tools or timeout change.

| Rows | Boundary and required outcome |
| --- | --- |
| BSD mount / spaces / empty / malformed / relative / truncated × three shipping parsers; self location | Execute actual embedded/nested functions and source definitions in a path with spaces, with PATH containing only python3. Invalid evidence refuses. No candidate action. |
| Shared class / exception / checked state, ABI, denied and loader rows | Single binding identity, pinned layout and numeric strict-read failures; simulated ctypes rows are labeled. |
| cwd / fd / fd-closed mmap / thread cwd | Owned native children supply PID and independent cwd/fstat ground truth; shipping scanner must BLOCK with exit3 and the matching reference/PID. |
| Idle | Clear only with complete evidence; otherwise UNKNOWN/exit2 with actual reasons retains. Public negative region coverage is currently unestablished. No idle PASS/eligibility claim. |
| Simulated EPERM / short / cap / churn / reuse / renamed inode / deadline | Test-side libproc/sysctl buffer/errno fixtures exercise shipping reader/scanner decisions. These are not native permission proof. |
| Real EPERM | Actual kernel errno/PID evidence if observed; otherwise explicitly NOT OBSERVED. No host exemption or elevation. |
| Darwin receipt schema | Complete schema accepts, wrong platform/missing or incomplete lane/Boolean counters refuse; schema fixtures are not authenticated-job proof. |
| Observer / protected mounts / snapshot / preserved | Shipping read-only commands and metadata API, no findmnt/procfs fabrication. Offline preservation and missing-owner observer refuse at their actual gates; snapshot/protected host evidence is checked separately. |

The approved preview proves individual observation gates only. Idle `eligible`
at the candidate-runner boundary is **not proven**. Never invoke candidate
cleanup or `observe ... run` as a dry-run. Source-native acceptance and real
EPERM observation are separate from simulated evidence. Frozen-app acceptance
is **NOT RUN/out of S4**; external PATH python3 remains the installed skill's
existing prerequisite. Scratch all-PID collectors are PR2, with EPERM retaining.

From an existing checkout/interpreter, portable fixtures (including synthetic
native syscall buffers) can run in under one minute without pytest:

```sh
python3 -B scripts/mac_native/suite_cleanup_process_use.py --fixtures
python3 -B scripts/mac_native/probe.py --suite cleanup-process-use --output /absolute/probe.json
```

The dispatcher cleanly skips on Linux (`platform=linux, skipped=true`). Native
Darwin requires the existing job-owned TMPDIR, records exact SHA/source hashes
and per-row expected/actual/provenance. A single 240s suite allowance includes a
reserved 10s owned-child cleanup window; command streams retain at most64KiB,
report at most32KiB. Children are released, TERM/KILL if needed, reaped and group
absence verified before fixture disposal; cleanup uncertainty exits nonzero.
Admitted kernel I/O is not hard-preemptible. Native execution is a separate
manager-owned leg: S4 budget starts 0/3, PR1 execution1, PR2 execution2, one
reserved correction; source changes never replenish or submit that budget.
