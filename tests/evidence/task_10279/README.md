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
