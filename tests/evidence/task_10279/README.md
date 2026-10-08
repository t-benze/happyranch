# TASK-10279 native admission, never merge

This continues TASK-10272 evidence recovery for the same draft PR1017. The
previous workflow and its actual failures are retained; this new exact-branch
push workflow first performs the newly admitted cheap native preflight on fresh
GitHub-hosted Ubuntu/native macOS15 runners, then runs the existing finite
ordinary-UID provisioning and accepted source coordinator only after successful
admission on that runner. Each matrix job has a finite 75-minute deadline.
Source results retain actual candidate/baseline exits and failure receipts;
submission or native readiness alone establishes no source behavior. It runs
no wheel/frozen build, browser, whole collection or suspended unit/proof body.

Candidate 3d41d128da9994f43aad0f02de9261b3416bcfa0 and original baseline
8378064e9933d5b3af4247eca55750ac427a564f remain immutable product source pins.
The new C source is independent test evidence, copied byte-for-byte from the
published same-PR helper change, and authenticated by its literal SHA256 before
compilation. PR1017 publication and GitHub read-back confirmed the candidate
pin. The complete evidence workflow/preflight/C/support source hashes bind the actual evidence commit.

Only the native executable receives existing passwordless sudo, only on these
disposable venues. Its only argument is original runner UID. Linux links
statically; macOS links only system native libraries with selected SDK headers.
Compiler/header/SDK/ABI/source/binary/loader dependencies and full invocation,
ordinary parent and root observer UID/EUID/exits are recorded.
The actual selected Xcode compiler/headers may be original-runner-owned;
root-or-original-owner, no group/world write, exact selected Xcode bundle and
image/version/path/hash evidence are required. Linux tools/headers remain
root-owned. Actual ownership is recorded; no host permissions are changed.
All real,
effective, saved (and Linux filesystem) UID owners retain cwd/executable/native
start identities; all native PID/PPID/PGID rows remain required. Native exit or
reuse is revalidated without signals. Incomplete/opaque living rows fail with
exact PID/operation/errno. No application code, credentials, environments,
process args/memory, commands/paths/refs as observer input, writes, signals,
host setting modifications, privilege service or invasive fallback.

The workflow is push-only to task/TASK-10279, contents:read, pinned existing
actions, credentials not persisted, attempt1 only, no self-hosted/main/schedule/
arbitrary inputs/secrets/OIDC/environments, finite timeouts/concurrency and
bounded artifacts including failure. Do not merge this branch. Preserve old
task/TASK-10272 and all original hosted receipts. No retirement/QA/CI waiver.

The execute-only macOS system sudo transport is authenticated without a byte
hash using a closed fixed-system-stat descriptor for `/`, `/usr`, `/usr/bin`,
and `/usr/bin/sudo`: root ownership, no symlinks or group/world write, regular
setuid executable, recorded device/inode/mode/UID/GID/size/mtime/ctime. The
identity is rechecked before invocation and after the initial probe. Linux
sudo retains its hash. Observer source/binary/compiler/SDK hashes remain
mandatory; no extra elevated command or file-permission change is admitted.
Actual run37839436634 had native exit0/complete411 macOS rows before failing
on ordinary receipt hashing of sudo; this repair does not assert final
admission or product readiness until new authentic receipts are inspected.

The ordinary bootstrap parent's native executable is authenticated separately
from `sys.executable`. CPython's macOS framework launcher executes the fixed
same-framework `Resources/Python.app/Contents/MacOS/Python` binary. Require
the exact versioned framework launcher and app paths, both byte hashes,
unchanged ordinary UID/native PID/start/cwd, and both binaries' linkage to the
same hashed framework library. Record these origins in `parent-origin.json`;
Linux retains exact launcher/native path equality. Installed runner bootstrap
Python is not the separately required provisioned CPython3.14.4 workload.
This changes no native observation or privileged operation. The official
launcher implementation is
https://github.com/python/cpython/blob/v3.14.4/Mac/Tools/pythonw.c .

Actual native-only run37840922877 at evidence
d935aed0fa91fde1a089e26524a3f3593edd9887 completed both admissions: Ubuntu164
rows with workloadUID1001 and macOS519 rows with workloadUID501; observer
UID/EUID0, native exit0, complete native tables and authenticated bootstrap
parent origins on both. Artifacts11578305071/11578170198 authenticate302606
bytes. This establishes that run's native readiness only.

The prepared source coordinator in `../task_10272/runner.py` now admits only
the exact task/TASK-10279 hosted push after its own fresh preflight. It verifies
run/image/UID/pins/descriptor and candidate C hash before provisioning, uses
the strict current candidate driver, and carries only the admitted descriptor
through the closed integration parent. `shipping-commands.json`, prefixed logs
and `shipping-result.json` preserve the native preflight inputs/results.
Baseline characterization overlays exactly the parent, driver, C observer and
retirement test file, recording the original parent hash and a distinct
test-only commit. All other baseline tracked files/links stay unchanged;
conftests, guard, executable stubs, daemon script and lock must independently
equal the candidate before the overlay. Preserve original baseline project
metadata; requirements/backend/groups agree and the only wheel inventory
difference is the accepted eight Assistant knowledge force-include removals.

The same-PR candidate is now published and observed at the immutable pin above;
the renewed workflow runs the fixed source stage after fresh native admission.
Ordinary focused shipping results require terminal actual hosted receipts;
partial runs do not establish complete source/cleanup acceptance. BOTH artifacts,
real isolated-daemon browser observations and side-effect-audited discovery
remain maker work; suspended proofs remain UNFULFILLED. No overall PASS,
independent review/QA, merge waiver or completed retirement follows.

Actual37846340997/56bfd91c again passed native admission (Ubuntu163/macOS436
rows) and ordinary official tool provisioning, but the aggregate source command
timed out after720 seconds on both platforms. Partial case lines are retained
as partial; baseline never executed. The finite coordinator now partitions
the unchanged31 candidate nodes and two baseline nodes into disjoint bounded
groups, with immediate diagnostics, authentic exact-case JUnit inventories and
complete native before/after source/stage attribution. See the task_10272
README for the300-second group deadlines and fail-closed cleanup boundary.
This changes only never-merge evidence source/docs; immutable PR1017 stays at
3d41d128da9994f43aad0f02de9261b3416bcfa0. New actual receipts remain required.

Run37843451408 at evidence79f1663408a7586bac4969322daf183bb26d02be
passed fresh native admission (Ubuntu162/macOS416 rows) and official ordinary-UID
tool provisioning on both runners. It then failed before source tests: the
preliminary overlay commit HEAD observation and the independent source manifest
HEAD check both requested `shipping-baseline-overlay-head`. The coordinator's
strict duplicate-receipt refusal is retained. The preliminary observation now
uses `shipping-baseline-overlay-created-head`; the manifest retains its separate
HEAD verification. Original failure receipts remain authoritative for that run;
the repair requires a new exact evidence push and fresh native/source receipts.

Run37844553544 at evidence49d9aa4789a1e0ece96d935945e31f2a72249b68
passed fresh native admission (Ubuntu163/macOS462 rows) and official ordinary-UID
tool provisioning. Source collection proceeded, but candidate31/baseline2 cases
on each venue failed in daemon setup before behavior: macOS nested uv could
not find Python, while Linux refused the stub guard identity. Original failures
remain in their artifacts. SAME PR1017 now binds nested uv to the actual parent
interpreter/venv and adds a finite ordinary-user stdlib identity probe before
pytest; this candidate was published and observed at the pin above after
JOB3969 exact-head active local Web/manual renewal exited0. This evidence push
renews that pin only; successful source behavior still requires new actual
receipts. Both fresh runners repeat cheap native admission before provisioning.
No source/R4/artifact/browser/discovery/independent QA PASS is implied.
