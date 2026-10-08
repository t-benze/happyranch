# TASK-10272 finite hosted source evidence

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
installed executable and stdlib hashes, and refuses missing native prerequisites.
No sudo, host installer, package upgrade, editable project install or downloaded
interpreter fallback is used. Exact uv0.12.5 and the accepted Hatchling1.32.4
closure are hash-installed from official wheels into distinct owned environments.
The locked PyInstaller6.21.0 closure is provisioned into a third environment.
Official wheel hashes, metadata and installed RECORD members must agree before
the source selection. All bootstrap commands, exits and bounded full logs are
recorded. These tools are provisioning evidence, not built-artifact evidence.

Candidate execution is exactly the accepted focused retirement command. The
baseline receives only the two candidate test-side files absent from baseline,
in a local test-only commit. Its tracked product source and lock must stay
byte-identical to the original baseline. The baseline runs only the two
same-root characterization rows, independently, with actual CLI callbacks and
closed test-parent roots. Source/base/test-overlay identities are recorded
separately. Failed callback assertions remain failed; no shared auth/session
repair, fake session, disabled worker or direct callback injection is admitted.
The candidate focused command includes the remaining refusal/legacy/lifecycle
rows and does not stop at the first failed row. All source stages are retained
with their actual exits; baseline execution follows candidate failure when
bootstrap readiness remains valid.

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

References: [official Python3.14.4 release](https://www.python.org/downloads/release/python-3144/),
[checkout credential persistence](https://github.com/actions/checkout/tree/34e114876b0b11c390a56381ad16ebd13914f8d5),
[upload action inputs](https://github.com/actions/upload-artifact/tree/b4b15b8c7c6ac21ea08fcf65892d2ee8f75cf882),
and candidate `docs/local-ci.md` retirement recipe. `tool-pins.json` records
official version-specific PyPI metadata URLs, wheel URLs and upstream SHA256s.
