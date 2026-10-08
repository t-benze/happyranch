# TASK-10272 finite hosted source evidence

Historical TASK-10272 runs and source remain preserved at their immutable
evidence refs. In this TASK-10279 recovery branch, `runner.py` is bound to
published candidate b2a9e565c940249703cb52aee3258317416ea477 for the exact
TASK-10279 hosted push after fresh fixed-native admission. The current coordinator
executes artifact recovery only; source execution is explicitly not performed.
Historical source receipts retain their original head and failures. See
`../task_10279/README.md` for the descriptor, separate logs/results and historical
four-file baseline source overlay.
Only that fixed C observer may use hosted sudo; provisioning, source imports,
tests, builds, browser and product execution remain ordinary UID. The older
unprivileged diagnostic below is historical and is no longer invoked by the
prepared source coordinator. Native readiness alone proves no shipping behavior.
The remainder of this document describes the original TASK-10272 source runs
and unchanged accepted provisioning recipe.

This branch is a task-owned verification entrypoint under TASK-10245 step6.
Never merge it or open it as an implementation PR. PR1017 remains on
`task/TASK-10262`. Its candidate, original baseline and observed current main
are distinct immutable pins in `runner.py`; checkout also pins product sources.
Only a push to `task/TASK-10272` runs this workflow. It exposes no ref, argv,
schedule, release, deployment, service, or input surface. Existing workflows,
dependency declarations, lock, backend and production modules are unchanged.

The runner has no general command interface. It builds official CPython3.14.4
from its hash-pinned python.org source in an owned user prefix on disposable
Ubuntu/native macOS15. It records actual OS/image/compiler/native origins,
installed executable and stdlib hashes, and refuses unavailable native inputs.
Ubuntu development and runtime archives are extracted only into an owned
prefix, using each observed installed runtime's exact version and authenticated
APT index SHA256/size. There is no update/install/upgrade, unconstrained version
selection or fallback. Download URIs, package control fields, original index
identities (excluding operational lock files), extracted files and links are
retained. The image's fixed `/etc/apt/apt-mirrors.txt` wrapping transport is
accepted only with a hash-recorded unchanged local mirror list, finite official
Ubuntu endpoints and exact index package path. APT still checks its authenticated
archive metadata; the extracted archive SHA256/size/control assertions remain.
macOS uses the existing selected Xcode SDK's ffi/sqlite headers and
library stubs plus Homebrew OpenSSL/xz/readline roots, explicitly passed to the compiler;
it records their origins without installing or changing them. A real compiler
link/execution precedes configure. The bounded config.log is retained even on
configure failure, and native dependencies of all installed extensions are
observed after the unchanged indispensable-module assertions succeed.
No sudo provisioning, host installer, package upgrade, editable project install
or downloaded interpreter fallback is used. Exact uv0.12.5 and the accepted Hatchling1.32.4
closure are hash-installed from official wheels into distinct owned environments.
The locked PyInstaller6.21.0 closure is provisioned into a third environment.
Official wheel hashes, metadata and installed RECORD members must agree before
the source selection. Package RECORD selection uses its top-level metadata;
setuptools' nested vendored RECORD files remain checked as payload by both the
outer installed RECORD and upstream wheel bytes. They do not count as additional
package-level metadata. All bootstrap commands, exits and bounded full logs are
recorded. These tools are provisioning evidence, not built-artifact evidence.

Candidate execution partitions exactly the accepted focused retirement suite
into nine fixed disjoint groups: four ordinary lifecycle/route/parser/concurrent
cases, four legacy groups of six/six/six/five cases, and each of the four
held-owner/nonrunning-owner parameter cases separately. The exact union is31
cases, checked from source AST and literal legacy variants without importing
test bodies. Baseline's two same-root characterization cases each run in a
separate parent. There is no new test body or whole-repo collection. The
baseline receives the explicit four-file overlay described above: the parent,
driver, fixed C observer and retirement tests, in a local test-only commit.
Its tracked product source and lock must stay
byte-identical to the original baseline. The baseline runs only the two
same-root characterization rows, independently, with actual CLI callbacks and
closed test-parent roots. Source/base/test-overlay identities are recorded
separately. Failed callback assertions remain failed; no shared auth/session
repair, fake session, disabled worker or direct callback injection is admitted.
Each group has a distinct owned closed stage and300-second command deadline;
the existing75-minute hosted bound remains unchanged. Pytest uses immediate
uncaptured output, standard native faulthandler diagnostics after90 seconds,
short tracebacks and a group-specific JUnit receipt. This is failure diagnosis,
not a duration/proof run. The bounded JUnit case identities must exactly match
the selected source nodes, with zero skips/errors/failures for a group PASS.
The strict command recorder retains timeout error,
actual exit and explicitly incomplete output prefix as failed evidence. A
nonrequired selection timeout no longer aborts the baseline by itself. Other
exceptions and output caps still stop admission.

Complete native censuses before/after each group retain every living row.
Ordinary-UID source/stage cwd/executable identities seed native descendant/PGID
closure; any attributed survivor refuses the next selection. This bounded
attribution is not all-host quiescence, a filesystem-read trace or a substitute
for the cases' actual launch/callback/terminal ownership assertions. The
elevated observer still only returns native JSON; cleanup is ordinary owned
process handling. Failed cases remain failed, and baseline follows candidate
failures only while native admission and closed scope cleanup remain valid.

Actual run37846340997 at evidence56bfd91c passed both native admissions and
official tool provisioning but exceeded the previous720-second aggregate
candidate deadline on both platforms. Linux retained27 passing case lines and
one same-root held-owner failure; macOS retained23 passing case lines and
stopped during the last legacy case. Neither candidate reached a terminal
pytest summary or final scope cleanup, and neither baseline executed. These
authentic partial receipts remain retained and are never full-suite PASS.
The fixed partition rerun is required to obtain bounded terminal results and
real failure detail; it changes no candidate/baseline pin or assertion.

Python units, surviving keeper proofs, RED/GREEN, mutations, repetitions and
duration runs remain SUSPENDED THR291seq5/16 and UNFULFILLED. General integration
remains SKIPPED THR243seq42. No whole-repo collection occurs here: the actual
environment-wide import/plugin/hook audit and zero-body observer are pending.
This workflow does not run or change any existing gate. Prior unchanged-head
local and hosted active checks remain attributable only to their actual scope.

After inspecting these real receipts, continue wheel and frozen daemon+CLI
build/installed-origin/behavior/native cleanup, and real isolated-daemon browser
evidence with the existing accepted driver/recipe. Provisioning must be repeated
on new ephemeral runners when needed; do not repeat successful unchanged source
checks without a named reason. These pending cases and any helper defects remain
maker work. Submission is not PASS. Independent full-diff code APPROVE and
behavioral QA PASS still follow readiness; every PR push renews those and all
active CI gates. Parent TASK-10245 owns merge/post-main/final delivery.

Receipt upload is limited to 64 MiB total, individual command logs to 8 MiB;
overflow aborts the owned process group, retains an explicitly incomplete
prefix, and is a failed run. It excludes full source/tool archives, venvs, live
tokens and credential directories. The final manifest records regular-file
sizes/digests and symlink targets without following them. GitHub run identity
and evidence source/workflow hashes allow independent attribution. Native
process snapshots come from the existing stdlib driver, not a mock observer.

Run37831893747 verified both official Python/tool installations, then failed
the unchanged process observer on Ubuntu's inaccessible same-owner cwd and a
macOS inaccessible PID. An early read-only native diagnostic now records
kernel ownership/start/link identities, errno and races; macOS also records
native effective/real-UID selections and bounded executable-name-only `ps`
observations for inaccessible PIDs. It reads no process arguments, environment,
memory or credentials and changes no process, privilege or host setting.
The candidate's unchanged `process_table` still decides readiness and fails
closed. Bootstrap Python is attributed only as the preflight observer; it
never supplies accepted CPython provenance. The later official-Python census
remains mandatory. A preflight failure stops before repeating successful
tool provisioning and retains diagnostic receipts without claiming shipping
readiness. The original failures remain in their original uploaded artifacts.
Relevant native interfaces: [Linux kernel proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
and [Apple XNU proc_info definitions](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h).

Run37833032960 preserved Ubuntu's same-owner `systemd`/`sd-pam` cwd/exe
EACCES and macOS's inaccessible native real-UID-selected PID. The native SDK
definition confirmed the helper's omitted `pbi_xstatus`; PR1017 now corrects
that ABI at observed candidate `e234b34d607821e70a6723bde6d0b42619f525f0`.
Its publication requires fresh active local/hosted checks and independent
verdicts. The immutable candidate pin is updated only after observing this
authorized remote head. Baseline/main and official tool pins remain distinct.
The auxiliary macOS `ps` query now selects only inaccessible native
effective/real-owner PIDs, bounded to64; all native table rows, including
foreign access failures, are retained. The previous whole-foreign-table `ps`
cap failure remains a failure. This diagnostic correction does not alter the
candidate's living-process refusal or authorize elevated privileges, ignoring
opaque same-owner processes, new infrastructure or a readiness/PASS claim.

References: [official Python3.14.4 release](https://www.python.org/downloads/release/python-3144/),
[checkout credential persistence](https://github.com/actions/checkout/tree/34e114876b0b11c390a56381ad16ebd13914f8d5),
[upload action inputs](https://github.com/actions/upload-artifact/tree/b4b15b8c7c6ac21ea08fcf65892d2ee8f75cf882),
and candidate `docs/local-ci.md` retirement recipe. `tool-pins.json` records
official version-specific PyPI metadata URLs, wheel URLs and upstream SHA256s.

Receipt-driven bootstrap corrections: run37831146574 stopped Ubuntu at the
fixed mirror wrapping URI and macOS at setuptools' multiple vendored RECORD
paths before any source shipping command. These are helper defects; both native
failures remain in their original artifacts. RECORD layout follows
[PyPA installed-project metadata](https://packaging.python.org/en/latest/specifications/recording-installed-packages/);
mirror attribution follows
[APT mirror transport](https://manpages.debian.org/experimental/apt/apt-transport-mirror.1.en.html).
These corrections change no tool/source pin, workflow, shipping assertion or
pending artifact/browser/discovery obligation.

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
# Current fixed artifact diagnostic recovery (TASK-10279)

Run37853314823 / evidence092496db791efabede6160add2bdd60606642dd5
at unchanged candidateb1f13ca65382a6fe169246648dd5dcea78780fde completed
candidate31/baseline2 source cases per venue, with29candidate passes and two
authentic same-root failures on both baseline and candidate. Its original
receipt manifests and failures remain controlling; main/baseline pins and all
active/suspended gate boundaries remain unchanged.

The current fixed main skips repeating those source selections and baseline
test overlays. historical-source-reference.json records the actual prior run,
evidence/source pins and per-venue original manifest hashes; explicitly marks
source execution this run as not executed and never source PASS. Artifact
builds retain distinct original immutable source manifests. After fresh native
admission and ordinary official provisioning, both wheel and frozen origins
execute the existing outside-checkout cases. Bounded fixed scenario daemon
diagnostics added in task_10279/artifacts.py retain actualHTTP500 tracebacks,
with fixture-token redaction, original log bytes/hash/completeness and protected
ordinary-owned paths. Missing/unsafe/over-cap diagnostics remain errors.
See that helper and task_10279 README for the exact closed file/size boundary.
No command/ref/path inputs, privileged product, host integration, application
imports on the live host, paused proof or broader collection are admitted.
Artifact success alone never resolves the historical source characterizations,
pending real browser/discovery, suspended keepers or independent verdicts.

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
