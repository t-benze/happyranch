# TASK-10279 native admission, never merge

This continues TASK-10272 evidence recovery for the same draft PR1017. The
previous workflow and its actual failures are retained; this new exact-branch
push workflow first performs the newly admitted cheap native preflight on fresh
GitHub-hosted Ubuntu/native macOS15 runners, then runs the existing finite
ordinary-UID provisioning and accepted artifact coordinator only after successful
admission on that runner. Each matrix job has a finite 75-minute deadline.
Historical source results retain their original candidate/baseline exits,
head identities and failure receipts; the current coordinator executes no source
selection. Submission or native readiness alone establishes no source behavior.
The artifact recovery below requires new actual wheel/frozen receipts;
it runs no browser, whole collection or suspended unit/proof body.

Candidate 0d498d535b779853470d007da4f9d4e4d0b2962b and original baseline
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

Prepared artifact extension after run37849177163: both venues completed all31
candidate and two baseline cases. Candidate28passed/3failed; baseline0passed/2failed.
All23legacy variants, ordinary fresh lifecycle/route/parser and genuine held/retry
refusal tails passed; concurrent empty-org expectation was defective (missing
existing broken:[]) and is repaired in SAME PR. Both same-root callback failures
remain failures with actual CLI/task/result/audit/native cleanup observations.

The fixed artifacts.py coordinator now builds wheel and frozen daemon+CLI from
each distinct immutable candidate/baseline source, then drives the existing
origin-bound parser/lifecycle/ordinary-callback/nonrunning-swap cases outside
checkout. Baseline only drives its separate same-root characterizations.
Hash-constrained wheel build and hash-locked runtime installation use the
accepted official user-prefix tools. Frozen uses the locked freeze environment
and an attributed no-follow tracked-source copy, with preserved file modes;
only its own copy may receive the accepted spec-generated hook. Both recursive
archives, literal TOCs, native dependencies, final bundle bytes/modes/links,
ordinary skills and installed wheel RECORD/console/interpreter are retained.
Actual native before/after attribution and each driver cleanup/callback receipt
remain mandatory. Build/validation/behavior failures are retained independently;
opaque census/residue stops further execution. Case command bounds are1000s,
with existing240s driver scenario bounds; builds/installations remain finite,
within unchanged75min hosted bound and64MiB receipts. These are prepared maker
helpers, not executed artifact evidence or a PASS. Costly provisioning still
requires fresh cheap native admission. Candidate publication/readback must
precede immutable pin renewal and new evidence publication. Real browser and
audited discovery remain pending; suspension/exclusions/gates stay unchanged.

Current immutable pin renewed after JOB3972 exact-head active local Web/manual
exit0 and actual PR1017 read-back at b1f13ca65382a6fe169246648dd5dcea78780fde.
Only the concurrent empty-org test expectation/docs changed in the candidate;
production code, native observer and tool versions remain unchanged. The
prepared wheel/frozen coordinator requires actual fresh dual-platform receipts.
Historical source characterization failures remain failures; browser/discovery
and independent gates remain pending. No overall PASS or retirement completion.

Actual run37853314823 at evidence092496db791efabede6160add2bdd60606642dd5
completed fresh native/tool admission and all31candidate/two baseline source
cases on both venues: candidate29PASS/2same-rootFAIL, baseline0PASS/2same-rootFAIL.
Concurrent org reads now pass; all23legacy and ordinary/held/nonrunning refusal
tails pass. Wheel and frozen candidate/baseline builds and authenticated origin
manifests completed; candidate parser/lifecycle/all23legacy passed outside
checkout. Every artifact callback scenario failed at ordinary CLI orgs init
withHTTP500 before any stub launch. Original failed scenarios and native cleanup
remain failures; they are not evidence of an artifact callback PASS.

The next fixed coordinator run retains a hash-bound historical source reference
to that unchanged candidate's complete source run. It does not repeat source
selections or overlay baseline tests, and does not claim fresh source execution.
Independent artifact builds use the original immutable candidate/baseline
tracked manifests. The new ordinary-user retain_daemon_diagnostics helper reads
only the fixed owned driver scenarios' daemon.log and their own daemon.token
after teardown. It requires regular, non-symlink, ordinary-owned protected files
and protected scenario parents, a1MiB complete-log cap, UTF8 and bounded token
length; records original bytes/hash/cap/completeness and token redaction count;
retains token-redacted text in bounded receipts. Missing/unsafe/oversized logs
fail diagnostics. No ambient credentials/environment/process argv/memory or
application modules are read. This closes the missing traceback receipt, not
the unknownHTTP500 cause. No production fix or fixture weakening is justified
until the actual traceback is inspected. Candidate/pins/nativeC/workflow/actions/
tools/permission/timeouts/caps are unchanged. No paused proof, browser or whole
collection is executed. Remaining maker work and independent gates stay open.

# Published reviewer fixture recovery at b2a9e565c

Actual37857635882/evidence921e26fbf retained dual native admission and completed
wheel/frozen builds, authenticated origins and candidate parser/lifecycle/legacy
checks. Artifact callback scenarios failed at org-init500 before stub launch.
The bounded authentic tracebacks identify an incomplete test skeleton missing
the default code_reviewer roster member; they do not establish a product defect.
SAME PR1017 now includes that static worker and engineering membership before
supported CLI org initialization. No default reviewer policy, auth, profile,
schema, executor admission or production behavior changes.

JOB3975 exact-head active local Web/manual renewal completed exit0. Normal push
and actual PR1017/remote read-back confirmed published candidate
b2a9e565c940249703cb52aee3258317416ea477 before this immutable pin renewal.
Fresh dual-runner native admission must precede ordinary official provisioning
and new wheel/frozen callback/nonrunning/refusal/terminal cleanup cases.
Historical37853314823 source results remain explicitly bound to
b1f13ca65382a6fe169246648dd5dcea78780fde and the original baseline and original
manifest hashes; they are not new-head source execution. The historical reference
records current_candidate separately and current_head_source_evidence=false.
Old500/same-root failures and the frozen caught memory-inspection warning remain
authentic. Real daemon browser, audited discovery, independent review/QA and
suspended UNFULFILLED keepers remain open. No retirement or merge waiver.

Only the existing fixed five evidence files change pins/reference/docs. Native C
source hash and tool recipe, exact branch push, contents:read, pinned actions,
no persisted credentials/secrets/OIDC/self-hosted/main/schedule/ref inputs,
finite75min/concurrency/caps/retention and NEVERMERGE status are unchanged.


Run37859744668 at evidence23ed203a91bdd5ac07427305cb12e5241b112fb4
passed both fresh native admission steps, but the ordinary artifact stages
failed and sealing exceeded the former 64 MiB raw-receipt cap on both venues.
No artifact was uploaded; no detailed behavior outcome is inferred from that
run. The candidate and tool/observer/assertion admission remain unchanged.
The finite transport now preserves all original receipts in a hash-bound
lossless gzip/tar archive capped at 64 MiB packed, independently 512 MiB
expanded and 20000 files. It verifies a complete streamed byte/digest roundtrip
before upload, retains originals, and refuses every overflow without omission.
The consumer verifies safe regular unique members and every original manifest
size/digest under the same limits. Shipping status/error/case exits and actual
seal byte/count diagnostics are also emitted to Actions logs so a sealing
failure cannot obscure its own boundary. These are diagnostics, never PASS.
A new exact evidence push and fresh dual native admissions must precede renewed
ordinary provisioning; original zero-artifact failure remains authentic.

The current pin renews the published test-side readiness repair at
0d498d535b779853470d007da4f9d4e4d0b2962b. JOB3979 authenticated supported
active Web/manual renewal at that exact head, exit0, before normal PR1017
publication and remote readback. The driver waits for all three regular owned
lifecycle files and the actual current child PID before native executable and
HTTP health validation, within the existing15s deadline. Old lifecycle files
are preserved; no daemon, auth, session or executor production behavior changed.

Run37862443466 at evidence4326a45ca71ee084d36e59663d9b9166bf1a07c2
retained complete dual native admissions and wheel/frozen parser/lifecycle
receipts. Complete archive verification recovered3507 original files totaling
169648074bytes. Actual callback/nonrunning cases still failed on reopen after
successful callbacks, durable history preservation and empty terminal census;
those observations informed the test-side readiness repair and remain failed
case results. Separate baseline and candidate same-root failures stay unchanged.
Fresh native admission and actual new artifact tails are required before any
readiness claim. This pin-only recovery preserves observer/tool/action/cap
contracts and historical source origins. Current-head source, real browser,
audited collection and independent review/QA remain pending; suspended keepers
remain UNFULFILLED. The evidence branch must never merge.
