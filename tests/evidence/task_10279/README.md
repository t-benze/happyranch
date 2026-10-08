# TASK-10279 native admission, never merge

This continues TASK-10272 evidence recovery for the same draft PR1017. The
previous workflow and its actual failures are retained; this new exact-branch
push workflow performs only the newly admitted cheap native preflight on fresh
GitHub-hosted Ubuntu/native macOS15 runners. It does not provision Python/tools
or execute product imports, tests, builds, installs or browsers. No PASS beyond
native readiness is possible. Both native results must be inspected before the
existing finite coordinator resumes costly work on newly admitted fresh venues.

Candidate e234b34d607821e70a6723bde6d0b42619f525f0 and original baseline
8378064e9933d5b3af4247eca55750ac427a564f remain immutable product source pins.
The new C source is independent test evidence, copied byte-for-byte from the
pending same-PR helper change, and authenticated by its literal SHA256 before
compilation. It is not yet a pushed PR1017 helper origin. The complete evidence
workflow/preflight/C/support source hashes bind the actual evidence commit.

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

This extension is prepared, not executed: the workflow remains native-only
until the authorized same-PR candidate is published and observed, its immutable
candidate/hash pins renewed, and the source stage explicitly added. Its
ordinary focused shipping results must then be inspected. BOTH artifacts,
real isolated-daemon browser observations and side-effect-audited discovery
remain maker work; suspended proofs remain UNFULFILLED. No overall PASS,
independent review/QA, merge waiver or completed retirement follows.
