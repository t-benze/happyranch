# First product E2E gate

This independently authored standalone lane implements the accepted E02/E03/E06
case design (`TASK-10476`, SHA256
`bc4c4c9d50a73ce45085ed6b5edcae572cdf75e8e26e3b583e6b7728037fd564`).
Source and design acceptance do not establish behavioral PASS. Actual disposable
runner results, authoring proof, independent review/QA and check enforcement are
separate delivery obligations.

Run only on an authorized disposable GitHub Ubuntu runner, through the durable
job/workflow boundary:

```sh
scripts/local_ci.sh e2e --python /absolute/supported/python --artifacts /new/output/directory
```

`run.py` refuses a live host or self-hosted runner. Python 3.12/3.14 run on PRs;
main/release use 3.12/3.13/3.14. Every cell builds the real Web on Node24 and uses
Playwright 1.58.0/Chromium with an exact hash-locked dependency closure. This
lane does not change Web dependencies or reuse the screenshot harness's mock
server. `browser-events.json` is an allowlisted response/status/page-error trace;
raw headers, bootstrap bodies, HAR, browser storage and raw trace archives are
never uploaded. PNGs contain only the rendered synthetic task state.

The closed temporary root owns HOME, runtime, two publicly initialized orgs,
registry, browser, sockets, provider plans and credentials. Source/hash-bound
adjacent wrappers call the actual CLI. The unchanged external stubs and guard
are reused as executable assets; neither `integration_parent` nor
`guard.install()`/sitecustomize is loaded. All retained pytest integration,
platform-marked and callback selections still require their original parent.
No old unit test is imported, collected, copied or executed.

The controller holds genuine provider processes while running the real CLI.
The daemon consumes their callbacks after normal process exit. A transparent
relay observes actual upstream HTTP200 and a newly reopened committed result
before dropping one downstream response. The next CLI invocation retries the
same payload bytes. SQL is read-only and assertions use fixed scenario data,
never product serializers as expected-value generators.

Lifecycle final-state readback uses a normal browser refresh after durable
completion, waiting for real detail and Recall responses in the same browser
session. The shipping detail/Recall queries do not poll while mounted; this
case does not establish automatic live-view refresh. The independent populated
alpha-beta-alpha cache case never reloads during org switching. The return
first verifies the cached rendered alpha state with its prior real HTTP200
provenance. It then navigates to the list, waits 31s for the shipping 30s query
staleness window, and revisits the detail through its real link, requiring new
detail/Recall HTTP200 and the same render/isolation assertions. No query cache,
clock or product state is changed. This explicit extra phase retains both the
populated-cache and fresh-response oracles; the case's original 30s target may
be exceeded, while the complete run retains its 600s target/900s hard cap.
Assertions cover alien task content/links in both list and detail. Assertions use
the shipping English heading `Recall tree` and raw status label `completed`.
The workflow explicitly installs its selected Python before resolving the
absolute interpreter; installation remains inside the cleanup-inclusive clock.

The outer watcher registers before launch, acts as a Linux subreaper, observes
only its descendants and exact admitted provider PID/start identities, and
tracks production-created per-session cgroups/units where present. It uses
pidfd signaling after identity checks, covers providers outside daemon PGID,
and requires process/unit/cgroup/port/socket/data absence. It does not provision
systemd or force a production backend: the test-owned provider descendant calls
`setsid()` before its launch witness so out-of-daemon-PGID coverage is exercised.
This retains the production-created containment membership. The watcher tolerates
only vanished proc entries during observation; other observation errors retain
full tracebacks and fail cleanup. It never sweeps unrelated host processes.
Missing backend capability fails
setup; unavailable/unknown cleanup fails the run. Catchable cancellation runs
cleanup; SIGKILL/runner loss cannot produce a successful receipt.

The separate `cancellation.py` supervisor runs Python3.12/SIGTERM and
Python3.14/SIGINT diagnostics on disposable Ubuntu. Each uses the same frozen
setup, real daemon, locked browser, public-created task and actual CLI callback.
After the upstream200 is committed, the deterministic provider remains held.
The launcher publishes its exact PID/start, active controller/daemon/provider,
owned identities, native units/cgroups and two listeners. The supervisor checks
those identities and sends the real signal through a pidfd to the launcher.
The launcher must record receipt and exit1 with FAIL. Only the separate
cancellation diagnostic may pass, after complete owned closure. A fake signal
receipt, already-exited provider, setup failure, missing identity or unknown
cleanup fails. Both diagnostics are required by the aggregate alongside the
unchanged eight-case/23-variant baseline; they do not count as product cases or
product-cancellation coverage. Full setup/action/interruption/cleanup/evidence
phase times and reaped child exit statuses are retained. Actual signal and
new-head baseline evidence remain PENDING until collected from hosted runs.

Target is 600s, action cutoff720s, cleanup reserve60s, evidence reserve120s,
hard cap900s including dependency installation and build. The workflow starts
the monotonic clock before checkout/tool setup. No budget exhaustion becomes a
skip. Every enumerated variant must be present with PASS, and failed/missing
matrix jobs fail the stable `Product E2E` aggregate. The aggregate's bounded
controls prove refusal of missing cells, skips, failed/missing variants, wrong
SHA, failed jobs and failed cleanup; these controls are not product evidence.

## Accepted case record

All cases use existing public boundaries with **no test-only product seam**.
The nearest retired route/unit coverage used seeded internal state; the retained
general integration suite is SKIPPED under THR-243 seq42. Web mocked/jsdom
coverage cannot establish live daemon or populated browser isolation.

| Case and implementation | Observable contract | Credible regression/control | Distinct coverage gap |
| --- | --- | --- | --- |
| 1 E02, `scenarios.journey`, `Store.finished`, `Browser.completed` | Public create → M1 delegate → W complete → natural M2 done; browser, public detail/Recall, result rows and ordered audit agree | Suppressed child enqueue leaves no W/final child; missing rendered outcome independently fails | Existing journey lacks built-browser final proof; ordinary browser task creation does not exist |
| 2 E06, `journey`/`deny` | Literal active replay200 retains exact row; terminal replay409 task_not_active | Duplicate insertion fails exact result count; reordered terminal gate fails response | Genuine held-process and terminal transport |
| 3 E06, `Relay`, `prove_committed`, `journey(lost=True)` | Real upstream200 + reopened committed row, lost response/nonzero CLI, identical retry200, one delegation | Duplicate append fails immutable row/effect counts; missing committed proof refuses drop | Actual post-commit loss, not pre-send disconnect or automatic CLI retry |
| 4 E06, `journey` | Changed active payload200 retains first delegate; changed terminal409 | Overwriting original decision fails row and downstream child oracle | Ordinary empty/static family, not v2/draft conflict semantics |
| 5 E03, `identity_denials` | Stale/fabricated known-slot409 session_mismatch; no-slot409 unknown_session; unknown-task404; no writes; genuine M2 succeeds | Wrong acknowledgement fails exact response; write-before-denial fails durable snapshot | Natural stale S1 under live S2, not unknown-task-only denial |
| 6 E03, `bearer`, owned positive control | Four exact GET/POST401 denials, genuine GET/POST200 and completed control | Omitted router auth fails exact denial/no-write oracle | Real shipping routes, not a minimal secured mock application |
| 7 E03, `collisions` | Public org-local IDs actually collide; both crossed real sessions409; both genuine completions | Wrong org dispatch fails response and dual-store snapshot | Crossed identity rejection, not concurrent success alone |
| 8 E03, `Browser.open_task`/`completed` | Same populated context alpha-beta-alpha, target HTTP, brief/outcome/Recall/status/href and no alien task content | Missing slug query key can show cached alien state and fails render/response | Real built browser cache and real org storage, not mocked providers |

`manifest.json` enumerates every variant. `observations.json` records commands,
HTTP bodies/status, launch identity, public/durable snapshots and relay evidence.
`result.json` records source, tools, phases, variants and cleanup. An incomplete
or missing receipt is failure, including a scenario exception before reporting.

Authoring evidence remains pending until actual attributable RED/control,
byte-exact restoration, final GREEN and usual five isolated/five sibling
repetitions are recorded for these cases on bounded disposable runs. No
protected auth/identity/product mutation is authorized by this harness. The
parent must resolve any proof-control authority gap before dependent execution;
an unrelated setup failure is never attributable RED. Keep evidence in this
case record/PR rather than inventing a second proof framework.

## Root-owned protected proof operations — not executed

The accepted four-answer case record above remains authoritative. The following
sizes its outstanding proof operations against the converged main source; no
operation is authorized or run by this child. The v24 obligation is credible
production-regression RED, byte-exact restoration, GREEN, **then** five isolated
and five sibling repetitions. Existing baseline, denial, binding and aggregate
controls do not satisfy production-regression proof.

| Case | Real source/symbol and intended temporary regression | Attributable assertion expected to fail |
| --- | --- | --- |
| 1 | `runtime/orchestrator/run_step.py::_consume_completion_report_body`, remove only `_enqueue_task_generation_aware(orch, child_id)` immediately after successful `try_delegate` | `journey` never receives the actual worker socket witness within30s; committed child exists, but no worker `session_start` or final closure. This is action failure after successful public setup, not bootstrap failure. |
| 2 active | `runtime/daemon/routes/tasks.py::submit_completion`, ordinary `prior is not None` / `v2_admission is None` replay arm: insert a second result through existing `org.db.insert_task_result` with the same task/agent/session and submitted summary/confidence/status/decision before returning200 | `unchanged` and `Store.result` reject an additional row; literal HTTP200 alone cannot pass. |
| 2 terminal | `runtime/daemon/routes/tasks.py::_require_task_active`, remove only terminal-status rejection while preserving unknown-task rejection | Literal terminal replay reaches the prior-result return200; `deny(E06.literal-terminal)` expects exact409/task_not_active. |
| 3 | Same duplicate-result operation as case2 active, exercised independently by `journey(lost=True)` after the relay records committed upstream200 and drops the response | `unchanged`/exact committed-row count fails on the real byte-identical retry; pre-commit/setup failures do not qualify. |
| 4 active | `runtime/daemon/routes/tasks.py::submit_completion`, same ordinary replay arm: replace read-only return with last-write-wins `UPDATE task_results SET output_summary=?, decision_json=? WHERE id=?` using submitted values and `prior["id"]`, commit before returning200 | Literal replay remains unchanged; changed payload fails `unchanged` and `Store.result(first)` original-summary/decision equality. Mutation of product persistence needs root disposition. |
| 4 terminal | Same terminal-gate operation as case2 terminal, independently exercise changed terminal replay | `deny(E06.changed-terminal)` rejects200 instead of exact409/task_not_active. An earlier literal replay failure is not this variant's proof. |
| 5 known slot | `runtime/daemon/routes/tasks.py::submit_completion`, replace only the pre-lock `expected != body.session_id` denial with unconditional `{ok: True}` return | Stale natural S1 under live S2 and fabricated known-slot cases independently fail exact409/session_mismatch. The mutation also affects case7; its earlier failure is not case5 evidence. |
| 5 no slot / unknown task | Separately replace the no-prior `unknown_session` raise with `{ok: True}`; separately remove the `task is None`404 guard in `_require_task_active` | No-slot must fail exact409/unknown_session; unknown-task must fail exact404/unknown_task (exception/500 is failure, never accepted). Keep both operations distinct from known-slot proof. |
| 6 | `runtime/daemon/routes/tasks.py::router`, remove only `dependencies=[require_token()]` | Four absent/wrong GET/POST requests independently fail exact401; unauthorized POST may create state in this disposable proof only and must fail the no-write oracle. No protected auth mutation was made. |
| 7 | `runtime/daemon/routes/_org_dep.py::resolve_org`, temporarily swap alpha/beta selection **only for completion-path requests**, retaining public init/create/read routing | Both directions of genuine colliding-ID crossed sessions must independently reject the erroneous200 and dual-store mutation. Ordinary setup must succeed first; a setup regression is not this proof. |
| 8 | `web/src/design-system/providers/_real-tasks.ts::useTask/useTaskRecall`, remove only slug from both query keys; retain request URL builders | Populated alpha→beta task click must fail target-org detail/Recall response or render sentinel assertions for genuine colliding IDs. Build/import failures do not qualify. |

This is twelve separately attributable operations: cases2/3 share one mutation
recipe but need distinct runs; terminal2/4 likewise; case5 has three recipes.
Each listed variant must be reached in an isolated case execution. The current
baseline is fail-fast and has no case-selection CLI: an earlier case7 denial
failure cannot be credited to case5, nor a literal replay failure to a lost or
changed replay. Root must size the minimal existing-harness case dispatch for
that campaign before execution; no new proof framework or product seam is
proposed. The full sibling baseline stays eight cases/23 variants.

Common restoration/containment command plan for **each** operation (proposal
only): bind `PROOF_BASE` to the final converged clean candidate; save
`git show "$PROOF_BASE:$PROOF_PATH"` and its SHA256 before any mutation. On an
isolated disposable proof checkout, restore with
`git restore --source="$PROOF_BASE" --worktree --staged -- "$PROOF_PATH"`, then
`git diff --exit-code "$PROOF_BASE" -- "$PROOF_PATH"` and compare the restored
SHA256 to the saved bytes. Verify **every** changed path, including both Web
query keys, before a clean committed GREEN source is launched. The existing
launcher requires clean committed source: a separate ephemeral RED commit and
a byte-restored GREEN commit are necessary; never publish a protected mutation
to this PR. Run only through the existing closed `e2e/run.py` disposable-runner
boundary with its900s hard cap and PID/start/unit/cgroup/port/socket/data cleanup.
A nonzero RED without the named assertion, a dirty restoration, or unknown
cleanup invalidates proof. Repetitions remain unexecuted until this disposition
and attributable proof are complete.

## Enforcement and uncovered work

The YAML defines triggers and a stable aggregate; it does not configure GitHub
rulesets. Required-check status must be read back independently at exact PR and
merge heads. Existing Web Node24, Linux Canonical with actual callback, macOS15
Canonical, Docs and applicable real hostile lab gates remain required. No old
unit or full historical G-run allocation is renewed. `local_ci.sh all` remains
retained Web only and cannot establish this lane's success.

Jobs approval/rejection/failure/single-resume, threads/replay, cancellation/late
callback, actual daemon restart/recovery, PR1014 compatibility and authentic
memory thresholds remain root-owned uncovered work. Provider hold and response
loss do not establish restart/recovery. No merge or deployment follows from a
successful run. Deletion PR1024 is merged at
`5e87e778c6968338a12bd5cd65d461e5699836be`; this lane preserves that deletion and
all current product/Web/shared assets. Main/release3.13 execution remains future
work, not evidence supplied by a two-cell PR run.
