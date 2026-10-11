# Daily product E2E health

This independently authored standalone lane implements the accepted E02/E03/E06
case design (`TASK-10476`, SHA256
`bc4c4c9d50a73ce45085ed6b5edcae572cdf75e8e26e3b583e6b7728037fd564`).
The bounded Pause, E04 and E05 increments below follow that original baseline closure.
Source and design acceptance do not establish behavioral PASS. Actual disposable
runner results, authoring proof, independent review/QA and check enforcement are
separate delivery obligations.

Founder THR-291 seq69 admits fresh E2E only from `.github/workflows/e2e.yml`'s
`0 19 * * *` schedule on default-branch immutable `github.sha` (19:00 UTC /
03:00 Asia/Shanghai). There is no local execution alias, manual dispatch, PR,
push, release, workflow_call or repository_dispatch entry. `run.py` and
`cancellation.py` reject absent/unsupported events, wrong cron/workflow/default
branch or checkout/source mismatch before launching scenarios. Actions metadata
validation is not protection against a hostile runner forging its environment.

Python3.12/3.13/3.14 baseline and TERM3.12/INT3.14 launcher diagnostics feed
`Daily Product E2E Health`, outside required merge checks. First scheduled run
is PENDING. Historical TASK10532/run38035241095 on `afb46cbb6` retains both
3.12/3.14 eight-case/23-variant baselines and TERM/INT cleanup; it is not fresh
head/daily evidence. Engineering_manager owns ordinary task/thread triage;
there are no retries, new notifications or promised response SLA.

Every cell builds the real Web on Node24 and uses
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
new daily baseline evidence remain PENDING until observed from the schedule.

Target is 600s, action cutoff720s, cleanup reserve60s, evidence reserve120s,
hard cap900s including dependency installation and build. Baseline scenarios
(bootstrap, baseline, Pause/restart, E04, E05, controller close/evidence together) have
at most480s; each other command and cancellation scenarios retain240s.
The launcher computes `min(command entry + cap, original start + 720)` once
before spawn, passes that exact end to the controller and uses it for its own
watchdog. No restart, case or phase resets the original clock. Exhausted
allowances refuse launch; actions cannot borrow teardown or evidence time.

Cleanup begins on normal completion, failure or catchable interruption. Its
end is `min(cleanup entry + 60, original start + 780, original start + 900)`.
Owned process/unit/group closure, all port checks and data/socket removal stay
inside that allowance. Sanitized text/PNG evidence is staged in memory before
removing its source, also inside cleanup. Serial per-unit query ceilings may
exceed60s; timeout fails honestly and retains observed ownership, without a
claim of absence. If cleanup fails, export may read partial diagnostics but
performs no more teardown or actions.

The first nonempty readable daemon token remains the redaction context through
partial data removal; later collection cannot erase it or replace sanitized
staging with raw text. If no trustworthy context was captured (missing, empty
or unreadable token), raw diagnostics and the detailed receipt are withheld.
Known-safe staging is retained where available; bounded incomplete metadata
reports FAIL, even when owned-root cleanup succeeds. This failure path does
not establish complete evidence or runtime sanitation safety.

Export has its own end `min(export entry + 120, original start + 900)`.
It writes a metadata-only FAIL first, then sanitized detailed artifacts and
the final receipt, keeping the last second of that same allowance for a bounded
failure receipt. Serialization, missing data or timeout cannot produce PASS.
No fresh120s is granted after900. Fractional `setitimer` alarms enforce positive
remainders without integer truncation. Timing receipts retain original ends,
command caps/entry/end, cleanup/export allowance/elapsed/outcome and failure
phase; both daily baseline and cancellation consumers require complete timing.
Elapsed export/total values are sampled at final receipt construction; its write
still runs under the export timer. Hosted upload shares the remaining outer
900s/15-minute job envelope; local timers cannot guarantee upload success.

The workflow starts the monotonic clock before checkout/tool setup. No budget exhaustion becomes a
skip. Every enumerated variant must be present with PASS, and failed/missing
matrix jobs fail the stable `Daily Product E2E Health` aggregate. The aggregate's
bounded controls prove refusal of missing cells, skips, failed/missing variants, wrong
SHA, failed jobs and failed cleanup; these controls are not product evidence.

This launcher correction is source delivery only. The illustrative future
combined375s estimate has105s nominal slack under480 only when global remaining
time permits; no runtime fit, signal/timer/cleanup behavior or E04/E05 assertion
validation has been observed. The baseline/Pause42 variants remain at their original boundaries; the E04 prefix below adds six. New deliberate
defects, RED/restoration/GREEN and repetitions remain UNEXECUTED/unauthorized.

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

The YAML defines the daily schedule and health aggregate; it does not configure
GitHub rulesets. Only `Source Boundary Lint` and `Web Lint (Node 24)` are automated
merge checks, both unconditional at the final PR head and exact merge on main;
read their status back independently. `local_ci.sh all` runs source-boundary
lint plus Web static checks, and cannot establish Product E2E success. Docs and
the real hostile lab are manual-only; retained canonical/platform/native and
actual CLI callback sources keep their fences and are not automated merge gates.
Independent full review, scoped QA, normal hooks and manager guarded merge remain
required. No old unit or full historical G-run allocation is renewed.

The E04 prefix below authors approval/rejection/exit7/timeout and both fan-in
orders; their behavior remains unproven. Wider jobs, threads/replay, cancellation/late
callback, restart windows beyond the bounded Pause increment, PR1014 compatibility
and authentic memory thresholds remain root-owned uncovered work. Provider hold and response
loss in the baseline do not establish restart/recovery. No merge or deployment follows from a
successful run. Deletion PR1024 is merged at
`5e87e778c6968338a12bd5cd65d461e5699836be`; this lane preserves that deletion and
all current product/Web/shared assets. The current schedule-only baseline covers
Python3.12/3.13/3.14, with TERM3.12/INT3.14 launcher diagnostics at 19:00 UTC /
03:00 Asia/Shanghai from immutable default-branch source. First daily execution
is PENDING. Historical TASK10532/run38035241095 at `afb46cbb6` supplies only the
3.12/3.14 baseline and launcher cleanup, not current-head or daily evidence.

## Cadence delivery and remaining test-authoring evidence (THR-291 seq69)

The existing four-answer case record and twelve protected deliberate-defect
operations above are preserved. All twelve operations, attributable RED,
byte-exact restoration/GREEN, five isolated/five sibling repetitions remain
**UNEXECUTED / unauthorized**. Baseline success and harness refusal controls
are not full assertion validation. This separate authoring gap does not impose
an obsolete E2E merge hold: independent full review/scoped QA may accept this
cadence/policy change without E2E execution/protected proof, and must identify
that exclusion. No complete behavioral/test-authoring acceptance is claimed.

Daily summary reports actual health/source SHA, exact artifact names and
failure/missing evidence; each artifact retains seven days. Missing/failed/
skipped/wrong-source/wrong-version cells, incomplete diagnostics/artifacts and
unknown cleanup fail health. Static merge checks offer no equivalent behavioral
coverage and delayed detection remains a risk. Wider jobs/threads/product
cancellation/late callbacks/actual daemon restart increments remain separately
reviewable root-owned daily work, unproven until real evidence. General
integration is SKIPPED THR243seq42. Archived THR297 Build11 remains FAILURE
143pass/3fail/40errors/28UNTESTED with two-org cause UNKNOWN; this baseline does
not fix fixture/systemd/interpreter gaps. TASK10582 owns detailed reconciliation.
PR1014 compatibility/authentic-memory obligations remain unproven.

## Bounded Pause source increment (THR-292 / TASK-10555)

`controller.main` executes the unchanged `scenarios.exercise` closure first,
then `pause.exercise`, inside the existing schedule-only launcher. The original
23 variants, 9 starts, alpha 6 tasks/8 results and beta 1 task/1 result are still
asserted before any Pause work. The manifest adds 19 separately attributed
variants (42 combined). `observations.json` records their inputs, CLI transport,
public projections, read-only durable snapshots, actual process identities and
host anchors; `scenario.json` passes named variants/phases to the existing
`result.json` and daily aggregate. Exact required-set/PASS validation remains
unchanged: absent, failed or skipped evidence fails daily health.

Authority is the current TASK-10555 consolidated step9 brief under
THR292seq14 approving9 amended13, seq18/23 and THR291seq69/87/88. It reuses the
accepted TASK10494 case design (SHA256
`fc214b0aee68c9b579add9040f62bb1d12744e471a9a9bd998d8a9f2cec5e353`),
TASK10496 DESIGN-ONLY PASS and manager acceptance. Only the external provider's
finite `code_reviewer` input and reviewer fixture body are amended; frontmatter,
teams, protocol, 300-second timeout, stubs, registry and enforcement are retained.
The genuine reviewer is its own normally created child with an actual APPROVE
callback; no worker is relabelled and no producer/session/history is seeded.

Each row records requirement, public action/scenario, fixed expected observation,
level, credible regression, nearest existing source and distinct missing seam.
Every row answers **test-only production seam: NO**. Historical Python units in
the accepted case map stay retired; none are recreated, imported or executed.

| Group / requirement | Variant IDs and action → fixed expected observation (fresh public CLI/API/browser E2E) | Credible regression caught | Nearest existing source; distinct missing coverage |
| --- | --- | --- | --- |
| 1 P01a/P05 chain/drain | `Pause.chain-drain`, `Pause.drained-before-restart`: public NULL-parent root; genuine manager delegates worker then reviewer; Pause generation0→1 with live worker → unchanged lifecycle/chain/links, authentic running worker capture/blocker. One normal worker completion/exit → original INTEGER result consumed, exactly one pending reviewer/no SID, quiescent real host receipts, paused with no blockers. Bounded socket/executable/audit/admission observations deny reviewer launch until Resume. | Missing descendant gate, fabricated capture, early Paused, suppressed callback settlement or duplicate reviewer. | `scenarios.journey` covers ordinary one-child continuation without hold; no chain/Pause/host-drain seam. |
| 2 P11/P14/P17 identity/idempotence | `Pause.child-pause-denied`, `Pause.child-resume-denied`, `Pause.pause-idempotent`, `Pause.resume-idempotent`, `Pause.stale-resume`: child controls409/root_task_required; repeated root Pause/current Resume200/changed=false preserves capture/generation/audit/queue/results/sessions in both orgs. After first Resume generation2 and genuine reviewer launch, new Pause generation3; stale Resume generation1 must409/control_generation_conflict and keep newer hold. Explicit Resume generation3→4 proceeds without duplicate work. CLI uses explicit org/generation and compares real response records without a one-wire assumption. | Name-based root scope, duplicate audit/toggle/enqueue, stale release or new session invented by retry. | Baseline E06 callback idempotence and retained Web `pause.test.tsx`/`write-path.test.tsx` cover different callback/UI seams; no real control generation arbitration. |
| 3 P14b/c same-org progress | `Pause.same-org-unheld`: while original worker/tree held, public distinct NULL-parent root in alpha → genuine launch, CLI callback, durable/public natural done with available capacity. Reads cannot create/update Pause controls. First hold/worker identity remains. Beta baseline remains unchanged. | Org-wide hold, blocked independent capacity, GET creates control or separate tree corrupts results. | Baseline `collisions` covers different orgs; missing two independently active roots in the same org under hold. |
| 4 P12 drained persistence/P01a finish | `Pause.drained-restart`, `Pause.restart-final`: verified paused/quiescent closure → pidfd TERM/wait on exact owned disposable daemon PID/start, start same shipping executable/same publicly initialized files, no re-init/DB writes. Control/generation/chain/child/results/consumption state exact; pending reviewer still has no launch/admission/SID. CLI Resume → one real reviewer APPROVE and one natural M2 done; original consumed results persist and exact start/end/consumption/chain counts agree. | Restart forgets hold, reaper falsely fails, replay of consumed callback, duplicate reviewer/M2 or no release discovery. | Baseline response-loss and `cancellation_hold` are transport/launcher cleanup; neither restarts the product daemon against retained files. Only this drained Pause window is authored. |
| 5 P01b separate drain Resume | `Pause.resume-during-drain`, `Pause.drain-final`: separate root/worker; Pause then Resume before worker callback → original PID/start/SID stays alive, hold clears; normal worker completion → exactly one reviewer/M2 path with 4 authentic results and 3 tasks. No restart here. | Resume kills/relaunches worker, callback duplicated, chain wakes manager early or diverges from restart journey. | `scenarios.journey` has no control while its worker is live; distinct from group4's drained restart. |
| 6 P16/P18 browser/readback/terminal | `Pause.overview-readonly`, `Pause.browser-pausing-en`, `Pause.browser-pausing-zh`, `Pause.browser-paused-en`, `Pause.browser-paused-zh`, `Pause.terminal-denied`: shipping Chromium responses at1440×900, user locale selector, root list/detail state/blocker/Resume, genuine child inherited/root link with no controls, alpha overview/read-only counts and restart limitation, real reload preserves generation1. Terminal browser retains completed and no Pause/Resume; both CLI controls409/task_terminal with unchanged state. `Pause.exact-closure` joins every launch/result/host count. | Synthetic-only UI masks transport drift, child control appears, locale/reload mutates state, GET queues work, missing org isolation/count honesty or terminal revival. | Retained Web `pause.test.tsx`, `pause-overview.test.tsx` and baseline Browser have synthetic projected/UI transport and ordinary live lifecycle evidence; missing actual daemon hold/restart bilingual readback. |

### Preserved Pause restart/count boundary

The E04 prefix below begins after these unchanged literal Pause boundaries.
The named process-local receipt reset retains the old boot archive; it is not
lost evidence or an arbitrary counter rebaseline. Host receipts expose aggregate/recent summaries,
not session IDs: serial phase deltas, exact admitted/released/published counts,
durable session audits and PID/start disappearance supply the join. Nothing
claims a receipt identity field that the API does not expose.

| Phase / named anchor | Actual launches since suite start | This daemon's admitted/released/published (quiescent) |
| --- | ---: | --- |
| Unchanged baseline closure / `baseline-before-pause` | 9 | 9/9/9 |
| First chain M1 ended; worker live / `restart-case-worker-running` | 11 | 11/10/10, active1 |
| Independent alpha root done; worker consumed; reviewer pending / `pre-restart-12` | 12 | 12/12/12 |
| `pause-owned-daemon-restart`: old PID/start verified exited, new PID/start ready / `post-restart-zero` | 12 | 0/0/0 |
| Explicit Resume reviewer live / `post-restart-reviewer-running` | 13 | 1/0/0, active1 |
| First tree reviewer and natural M2 done / `post-restart-first-chain-2` | 14 | 2/2/2 |
| Separate drain case M1 ended; worker live / `drain-case-worker-running`, `resumed-draining-worker-4` | 16 | 4/3/3, active1 |
| Final closure / `final-post-restart-6` | 18 | 6/6/6 |

Exact combined alpha closure is13 tasks/17 results; beta stays1/1. Each chain
has3 tasks/4 results, root count2, each child count1, four starts/ends/consumed
reports, exactly one chain advance and delegate→done manager decisions. Final
socket holds are empty and every actual provider PID/start is gone. The outer
unchanged watcher still owns all browser/relay/socket/old+new-port cleanup.
Controller phase evidence retains old/new daemon identities and cumulative
ports even if restart readiness fails. No serving daemon is touched.

### Authored versus executed

All six groups and bilingual live browser aspects above are **AUTHORED,
UNEXECUTED**. First scheduled daily execution remains **PENDING**; local syntax,
registration/config inspection and lint cannot establish behavior or complete
test-authoring PASS. Timing for this combined source is unmeasured. Manifest
600-second target, 720-second action stop, 900-second hard limit, evidence/cleanup
reserves and provider300 remain unchanged; there is no ad hoc/direct controller,
provider, browser or disposable-daemon execution, rerun or forged event admission.

All twelve inherited deliberate-defect operations and any Pause proof are
**UNEXECUTED/unauthorized**; no RED/restoration/GREEN or five-isolated/five-sibling
repetitions. Authentic held-queued workflow history remains **UNAVAILABLE**;
six P07/G4 capture interleavings, other restart windows, v2/workflow histories,
crash-after-Resume-before-enqueue and unscheduled/unimplemented cases remain
**UNEXECUTED**. Launcher TERM/INT cleanup is not product cancellation/restart
coverage. General integration is **SKIPPED THR243seq42**, never PASS; former
Python units remain **RETIRED**. No compatibility/authentic-memory/deployment or
feature-completion acceptance. Full independent review, source/static scoped QA,
exact-head two-lint receipts, normal hooked publication and manager guarded merge
remain separate delivery gates. No production schema/auth/permission/enforcement,
registry/shared stub, dependency, cadence/cap or cleanup-machinery changes.

## E04 jobs prefix (TASK-10686 / THR-291)

The daily controller now runs the unchanged baseline23, then Pause19, then
`jobs.exercise`'s six serial variants: **48 authored variants**. E04 source
reuses accepted design SHA256
`14cf2584b91514bfaee04e59f23f862429e2c865179c298167e88df7b9b7cdf1`
and composition SHA256
`68f4a10b8822fdf109cea03949d1babb1105210004184bb98a2486294f3e1eb9`.
PR1036's merged limits supersede the old designs' internal240 baseline limit.
The common controller/watchdog deadline is min(entry+480, original720);
per-case limits20/15/20/25/25/25 and read/socket caps only shorten it.
Other commands/diagnostics240, cleanup60/export120, target600/hard900 and
hosted15min remain. Fit, closure and sanitation behavior are UNPROVEN.

The preserved baseline closes at9 providers/alpha6tasks8results/beta1/1.
Pause archives bootA12, binds the actual same-files old/new daemon identity,
then closes bootB6/cumulative18 with alpha13tasks17results/beta1/1.
E04 before/S1/final bootB triples are(6,7,8),(8,9,10),(10,11,12),
(12,13,14),(14,15,16),(16,17,18). Final cumulative30=A12+B18;
alpha19tasks29results/beta1task1result, eight jobs/seven script starts:
three completed0, three completed7, one rejected, one failed/timeout/-9.
E05's53/37/B25 is future scope, not this prefix.

For every row below: level is real public CLI/browser/durable E2E;
**test-only production seam: NO**. Only the external owned provider and
submitted deterministic Python scripts gain synchronization. No DB seed,
internal enqueue, fake SID, production import, registry bypass or product edit.
Each credible mutation is an unexecuted sensitivity proposal, not authorization.

| Named case / contract and action → literal oracle | Credible regression | Nearest coverage and missing seam |
| --- | --- | --- |
| `E04.approve-exit0`: provider CLI submits review-required cap30, blocks/exits/parks; real browser Approve & run after S1 settlement; owned script released after running readback → completed/0/NULL, exact E04_OK streams, founder audits; natural live S2 reads then final callback/exit/done. | Premature run before review; lost terminal wake. | Baseline task completion does not submit or run a job. |
| `E04.reject`: same real submit/block, browser Reason=E04_REJECTED_BY_REVIEW → rejected/reviewer founder, all execution fields NULL, no sentinel/peer/files at submit/park/dialog/final drain; one truthful natural S2/done. | Rejection runs script or omits wake. | Baseline has no rejection; empty output alone cannot prove no launch. |
| `E04.completed-exit7`: real cap30 Python exits7 → completed/7/NULL, exact E04_EXIT7 streams, completed audit; CLI/receipt/browser and final summary explicitly preserve nonzero. | Normalize exit7 to0 or label natural nonzero failed. | Ordinary provider exits do not cover job runner return codes. |
| `E04.failed-timeout`: real cap5 direct script ignores TERM and is never controller-released → product timeout/KILL, failed/timeout/-9, exact streams, duration5–17s, PID/start gone, empty PGID and socket EOF before outer cleanup, one natural S2. | Omit cap forwarding, discard reason, or accept terminal row without process closure. | Launcher TERM/INT diagnoses harness cleanup, not product job timeout. |
| `E04.two-jobs-A-first`: A completed0 while B remains pending across durable/audit/CLI/browser and two +0 settled checkpoints; B then completed7 → one S2, both literal outcomes, two results/count2, no third provider. | Coordinated all→any producer/admission predicate or lost claim fence. | Single-job wake and ordinary child joins do not cover job fan-in. |
| `E04.two-jobs-B-first`: B completed7 first, A pending through the same checkpoints, then A completed0 → identical final truth with reverse triggering-job ID. | Asymmetric terminal ordering, premature wake or duplicate return. | A-first alone cannot establish order symmetry. |

`provider.jobs_command` invokes the bound CLI as a genuine provider child,
using the real issued task/session. S1's final CLI action is blocked callback;
S2 runs show/receipt/output/tail/wait, then exactly one completed/done callback
as its final CLI action and naturally exits. Read-command exit0 never means
job success. `Controller.provider_cli` retains command/argv/child PID-start/
parent PID/source/interpreter/payload/HTTP receipts. Existing org discovery is
retained and reconciled without assuming one request per read. For each of all
six variants, `validate_e04` requires the shipping method, exact alpha/job path,
query (including genuine S2 binding), literal HTTP200 and actual body: human
show GET detail, JSON show/output GET receipt, tail GET and wait POST. It
compares full detail/wait rows (including the captured output cap), the closed
receipt identity and exact output/tail bytes against raw durable evidence;
CLI JSON/text must agree with those responses. Discovery-only or error wire
cannot satisfy a job read. `jobs.exercise` and the aggregate both require the
first human-show line to equal returned ID, literal status and created timestamp;
a completed/failed word in the title cannot substitute. Independent literal
exit7, timeout/-9/reason and rejection-reason lines remain required.

The live-S2 frame now retains source, actual provider identity/PID-start and
snapshot entry/finish times. The aggregate requires one in-progress task bound
to S2, exactly the unchanged blocked S1 result, literal terminal job rows and
the natural S1-consumption → wake → S2-start audit chain, with no S2 end/result.
Its snapshot must precede all actual S2 reads and the final callback, and its
result/audit rows must reconcile with the final durable snapshot. Missing or
contradictory live frames fail. Completed-only
`Store.result` is not used for S1; `Store.jobs_parked` and `jobs.final_task`
inspect literal blocked/completed results and original historical wait IDs.

`jobs.pending`, `run_script`, `terminal_oracle`, `final_task`, browser job methods
and the six records map actions to literal independent SQLite/file/process/UI
oracles. Script tokens and hashes are distinct from genuine task/SID/PID joins.
`jobs.anchor` retains two fresh settled public metrics projections and all
intermediate samples, with exact admission/release/publication counts, ordered
newest-first tails including duplicates, no eviction and no unexplained reset.
S1 fully consumes/parks/publishes/releases before browser mutation; no S2 host
receipt is required while S2 is still live. `Controller.observe_providers`
continuously records socket readiness and executable witness counts across
bootstrap, both boots, inter-case gaps and final drain. Its failure or missing
observations fail health. This is serial causal attribution under the closed
synthetic workload; host summaries contain no SID/PID/unique receipt identity.

Evidence/control authoring records (all four answers; production seam NO):

| Named fixture/control or consumer | Contract / assertion mapping | Credible regression; nearest coverage/gap |
| --- | --- | --- |
| Provider jobs protocol + wrapper child witness | `jobs_command`/`provider_cli` order absolute child/exchange deadlines with one-second receipt delivery room, append partial timeout evidence before refusal, bind actual child PID/parent/argv/source/SID and enforce block/final last-CLI rule. | Equal caller/child timeouts lose streams/identity; controller impersonation or post-final CLI. Exit-only provider lacks these command/evidence boundaries; persistence and sensitivity are unexecuted. |
| Owned submitted-script socket/sentinel | `pending`/`run_script` bind exact script/token/job to SO_PEERCRED, E2E_ROOT, PID/start/PGID, natural EOF/closure and untruncated streams. | Launch before approval, wrong peer, controller substituting timeout; baseline provider socket is a different process family. |
| Passive observer + boot/window records | `anchor`, passive Pause archive hooks, unchanged baseline/Pause boundary assertions and `aggregate.validate_e04` recompute A0→9→12/B0→6→18, serial+1/+1 and pair+0, full tails/counters and observed source identities. | Missing/reset/evicted window, extra attempt without provider, duplicate summaries deduplicated, receipt-before-S1/after-S2 confusion; old aggregate only checked metadata. |
| Browser jobs fixture | `job_action` captures actual unchanged-cap body, status, dialog-open no-launch; `job_terminal`/`jobs_waiting` read fresh shipping detail, output, Recall and screenshots. | Wrong dialog/action, stale outcome or exit7 laundered; existing Browser covers only ordinary tasks. |
| Actual-content daily aggregate | `validate_e04` reads observations and browser events, resolves records and screenshots, recomputes source/boot/counter/CLI/job/audit/process/result joins, five method/path/query/HTTP200/body/stdout/durable read joins, source-timed live-S2/blocked-S1 chronology and exact human-show first header; rejects empty/hash-only/disagreeing content. | Discovery-only/error/contradictory job wire, missing live S2, title masquerading as status or forged PASS/count-only content; metadata controls cannot validate these joins. Dedicated synthetic E04 aggregate fixtures are not authored or executed; sensitivity remains MISSING. |
| Existing metadata `controls` fixture with48 manifest entries | Existing `validate` controls still exercise missing/failed/skip/source/cleanup/timing envelope, now against the48-ID manifest. They are explicitly metadata-only; daily main additionally requires actual-content validation. | An omitted E04 name slips past stale manifest; old42-ID fixture covered baseline/Pause only. No control was executed for this delivery. |
| Failure export / redaction context | `Controller.sanitize` + private retained old/new bearer context feed `run.collect_evidence/redact` before observations/scenario/browser-event/failure export; PR1036 unknown-context withholding and reserves remain. | Old boot secret survives final-token-only redaction, or failure loses partial chain; original exporter retained only final bearer. |

Consumers are `Controller.mark/durable/evidence` → observations.json and
scenario.json → run.py result.json/sanitized export → existing five seven-day
artifacts → aggregate.main/validate_e04. Partial records and CLI attempts are appended before
success marks. R1 uses a single absolute exchange deadline set by the controller
before send: min(now+10, case end, common scenario end). The provider's absolute
subprocess deadline is one second earlier; manifest/payload/spawn work consumes
that same allowance, and communicate uses only its remaining time. A call with
at most one second remaining refuses before dispatch. Socket send/read consume
the original exchange end, never a fresh ten seconds. On subprocess timeout,
the provider returns actual child identity and partial streams with explicit
timed_out/non-success before raising; the controller appends that receipt before
asserting success. The one-second delivery room covers serialization/transport
inside the existing cap, not cleanup/export reserves. Existing sanitized
finally export carries the partial receipt and executable witnesses, even
without a final case record. Unknown secret context still withholds unsafe text;
OS scheduling/transport loss or watchdog termination is not a persistence or
runtime-fit guarantee, and delivery/sanitation proof remains UNEXECUTED. Cancellation uses the
preserved exit-only provider and independent diagnostic shape; it does not
require an E04 case or its content validator. The outer Owned mechanics and
all old/current units remain unchanged; recorded job peers join its existing
PID ledger without discarding earlier peers. Exhausted cleanup fails visibly.

Delivery verification is SOURCE/CONFIGURATION/STATIC ONLY. New cases, controls,
assertion sensitivity, timing fit, cleanup/sanitation, all protected12 deliberate
mutations, RED/restoration/GREEN and repetitions remain **MISSING/UNEXECUTED**.
No harness/product/provider/browser/job execution or new aggregate control run
is authorized here. First daily remains PENDING per TASK10555/JOB4257 owner;
that collector is untouched. General integration SKIPPED THR243seq42; Python
units RETIRED. Daily19UTC, Python3.12/3.13/3.14 plus TERM3.12/INT3.14 and only
Source Boundary Lint/Web Lint(Node24) merge contexts remain. No deployment,
Jenkins, compatibility/authentic-memory PASS, E05 or program completion follows.

## E05 threads and replay: authored source only

The accepted TASK10637 design (`832d218b693b6e6f660a2f4ca484f498cf452878ae5f0e092d278986d0924f40`)
and TASK10653 composition addendum govern four cases/five variants. The entire
23 baseline +19 Pause +6 E04 prefix runs unchanged before E05, giving53 source
variants. E05 adds three public-created threads, eight messages, six genuine
thread invocations and one genuine dispatched task/result: seven providers.
C/N/D settle at bootB21/22/25; archived bootA12 stays separate, cumulative37.
Final alpha20tasks30results/beta1task1result, eight prior jobs/seven scripts,
B19task+6thread summaries and four settled delivery pairs are separate oracles.
These counts are source expectations, not observations.

| Case / variant | Contract and literal oracle | Credible regression (unexecuted) | Nearest coverage and distinct gap | Production-only seam |
| --- | --- | --- | --- | --- |
| Compose / `E05.compose-race` | Shipping dialog, same synchronous Enter/click/click batch, one actual POST/200/committed opening/detail entry | Duplicate POST/thread if submit guard is removed | Retained Web write-path mocks lack real SPA/proxy/SQL join; eligibility alone cannot prove guard reentry | NO |
| Reply / `E05.reply-natural` | C has two messages, M1/W1/W2 genuine tokens, claimed1..1/1..1/2..2, one consumed/two declined; natural closure | Lost wake, duplicate reply or unsettled delivery | Retained thread integration lacks this exact public/browser/durable closed workload; baseline task replay uses different authority | NO |
| Dispatch / `E05.self-dispatch-followup` | D has one source-linked NULL-parent Engineering task/result, system2/reply3/system4/followup5; no replacement | Duplicate dispatch, lost followup, recursive task | Retained followup integration has broader seeded cardinalities, not this finite public-created venue | NO |
| Denials / `E05.consumed-token`, `E05.nonparticipant-task-send` | Same live M1 replays the same file for409 consumed; actual live task SID sends to worker-only N for403 not_a_participant; coherent before/after content unchanged | Removed/reordered token or participant gate permits another message/effect | Baseline E03/E06 covers task callbacks, not thread invocation replay or this participant boundary | NO |

`thread_provider.py` spends only its delivered token through its own bound CLI
and private payload file; the task plan uses its own real task/SID. Controller
instructions carry finite literals, never replacement authority. SHA256 token
aliases, actual socket PID/start, genuine CLI child/source/argv witnesses and
read-only durable identities remain distinct from anonymous host summaries.
Private files are removed before normal provider exit. No DB writer, product
serializer oracle, altered roster, stub/registry bypass or provider substitution
is used. Same-thread nonparticipant reply authority is not publicly constructible
here; the actual task-session send denial is the named boundary exercised.

The E05 browser context uses its own registered loopback P, forwarding to the
actual daemon D; existing CLI relay L and original browser context remain.
Only the first exact compose request's original200 response can be held after
an independent commit, with one arm deadline at most5s. Ordinary content and
SSE are forwarded as bytes, without bootstrap/auth substitution or manufactured
responses. Partial evidence includes actual network/navigation/eligibility
records. Disabled controls do not prove handler reentry or causal guard
sensitivity. Owned context, listener, connections and workers close before daemon
teardown; missing/unknown closure fails daily health.

The changed fixture/consumer contracts are: controller plan/peer/source registration
must reject unexpected authority (nearest E04 task-only plan); the provider's
finite CLI protocol must preserve same-file replay and final-callback ordering
(nearest jobs protocol); Browser/BrowserRelay must preserve real bytes and the
original held response (nearest baseline CLI loss relay cannot serve a whole
browser origin); Store.thread_frame must retain one reopened mode=ro/query_only
transaction across all affected tables and file manifests (nearest separate
Store.query calls are not a coherent negative frame). Credible regressions are
respectively wrong-owner callbacks, replay-file substitution, response fabrication
or loss, and false unchanged-state evidence. Every one has NO production-only
seam. These are external fixture/source assertions, with sensitivity unexecuted.

Controller observations and scenario references flow through unchanged run.py
result merge/sanitized export into the existing five artifact directories.
`aggregate.validate_e04` validates the original30-provider checkpoint from its
actual retained witness prefix; `validate_e05` independently opens the E05 browser
artifact and actual observations, recomputing literal messages, claims, denials,
source/CLI joins, ordered anonymous tails and closure. Missing/empty/hash-only
or contradictory references fail. The aggregate's regression is false healthy
reporting from metadata/totals; nearest E04 consumer lacks thread/proxy content.
The manifest adds exactly five IDs, preventing an omitted E05 case from passing.
NO production-only seam is added by either consumer. Validator controls are not
product defect proof; no new controls were executed.

The R1–R3 correction extends these same four-answer records, without a new
proof framework or variant. All source predicates below are authored; dedicated
contradictory-receipt/browser-ledger/sanitation controls are **UNAUTHORED** and
assertion sensitivity remains **UNEXECUTED**.

| Existing case or fixture/consumer | Required contract | Credible regression rejected by named source predicates | Nearest coverage / remaining gap | Production-only seam |
| --- | --- | --- | --- | --- |
| Compose / `E05.compose-race`; Browser snapshots and `BrowserRelay` | Complete chronological event ledger, exact held/released prefixes and actual proxy release interval; zero premature navigation, one automatic entry, four paired intentional revisits; actual initial GET/messages and rendered opening | A premature event omitted from a case snapshot, duplicate automatic navigation or empty/wrong opening fails `R2` prefix/navigation/projection/bubble checks | Earlier PNG and snapshot membership checks did not validate chronology or rendered semantics; same-tick eligibility is retained, guard sensitivity UNPROVEN | NO |
| Reply / `E05.reply-natural`; provider and aggregate | Every decline/reply equals its canonical receipt and original instruction; exact argv, request, route, status, selected response, exit and shipping stdout; both literal C messages in their own sequence bubbles | Accepted SQL state with exit1/KeyError on decline, wrong endpoint/body, detached receipt or omitted rendered reply fails `R1` outcome and `R2` bubble checks | Earlier identity/source/terminal-row joins did not prove positive CLI delivery or actual rendering | NO |
| Dispatch / `E05.self-dispatch-followup`; N/D compose, task read and final callback | Full founder compose command/wire joins; exact dispatch and replies; D2 GET returns this completed task/result/summary and prints literal details; callback remains last; full D system payloads and distinct rendered reply/summary | Wrong task/details summary or an absent D reply hidden by the completion system summary fails `R1` task/read and `R2` per-bubble/system checks | Earlier two-substring D check and selected-response presence did not establish all literal content | NO |
| Denials / `E05.consumed-token`, `E05.nonparticipant-task-send`; Relay E05 request capture | Exact original instruction/real409 or403/request/response/error stdout, same retained file/live owner and coherent unchanged frame | Another endpoint/status/body or detached denial cannot pass by retaining valid durable rows | Existing negative frame checks are retained; dedicated contradictory artifact controls remain unauthored | NO |
| `thread_provider.redaction_context/command`; Controller sanitation/export closure | Validate private retained context before and after capture, including current/old bearer and invocation tokens; sanitize full binary streams before16384-byte clipping or JSON/socket output; original byte counts/flags, safe digest identities | Boundary-straddling credential, multibyte output, timeout suffix or missing/unsafe/lost context cannot take the old clip-before-redact path; unknown context raises fixed text and withholds output | Existing full-string exporter cannot repair a fragment already clipped by a producer; sanitation and timeout behavior remain unproved | NO |
| `aggregate.validate_e05`, threads command/compose/settled producers, browser response/DOM producers | Reopen existing artifacts and recompute actual command, public response, full ledger and rendered content joins; PNG signature proves file format only | Metadata-only or valid durable rows cannot substitute for public outcome/content | Extends the existing actual-content consumer; no executed controls or full test-authoring PASS | NO |

The CLI relay captures request JSON/response text only after E05 starts; the
original baseline/Pause/E04 wire receipts keep their prior shape. The controller
sanitizes retained observations with the complete credential context; the
unchanged launcher keeps its unsafe-export refusal. Binary provider capture
preserves original stdout/stderr byte counts and per-stream cap flags. Complete
secrets are removed before clipping; timeout capture also withholds a trailing
credential prefix, and UTF-8 clipping drops incomplete codepoints. Private
context failure emits only a fixed error and no captured output. Token/payload
digests remain identity evidence, never credentials or sanitation proof.

All E05 behavior, assertion sensitivity, proxy compatibility, guard reentry,
token sanitation, delivery/persistence, process closure and measured budget fit
remain MISSING/UNEXECUTED. Protected12 deliberate defects, RED/restoration/GREEN
and repetitions remain unauthorized/unexecuted. No full test-authoring PASS.
C18/N8/D25/final4 share the existing480 scenario allowance clipped by original720;
cleanup60/export120 and hard900/target600 are unchanged. Arithmetic is not fit
proof. Daily19UTC source binding and all five cells/artifacts remain unchanged.
General integration is SKIPPED; Python units are RETIRED. TASK10555 owns the
first-daily collector and Pause acceptance. Later cancellation/late-callback/
restart coverage and eventual daily acceptance remain root-owned and unproven.

## E07 C source increment — TASK-10773

Accepted TASK10763 design
`5e9f7f401389841748b0e018ff9988e126f7a2511dabcb140d19ee2e6dc580a7`
adds C only after the fully evaluated and archived E05 end. Exactly three
variants bring the authored total to56: `E07.cancel-cascade-browser`,
`E07.cancel-terminal-unchanged`, `E07.cancelled-late-cli`. These are source
assertions, not observed behavior. R/full composition remains HELD; no crash,
new boot, credential rotation, or dormant restart implementation is included.

P publicly delegates to W using its real bound provider CLI, then exits
naturally. The one INTEGER parent result is consumed as delegate. W writes
its own bound final payload and launches the actual hash-bound CLI wrapper.
A dedicated one-shot relay captures the complete original completion POST
before upstream transmission; provider acknowledgement precedes communicate.
Live PID/start/PPID/argv/hash and actual /proc socket inode/FD/TCP tuple join the
issuer to that request. The browser Cancel dialog sends `E07_CANCEL_TREE`.
Product teardown must close W, its CLI and launch root before Fcancel; cleanup
cannot satisfy the case. The original retained POST is then forwarded once to
boot B: actual409 `task_not_active`, cancelled true. Dead downstream EOF and the
upstream response are distinct; no killed CLI response/exit is fabricated.

| Case or changed evidence consumer | Contract | Credible defect | Prior gap | Shipping seam |
| --- | --- | --- | --- | --- |
| Cascade browser, SQL/public frames and receipt assertions | Two cancelled tasks, exact shared timestamp/note/two founder audits; P result retained, W result absent; real terminal UI; one active cancelled receipt, no queued cancellation | Wrong cascade/status, lost result, result race overwrite, surviving child or parent resume | Original53 and launcher interruption do not cancel a live public product tree | Real create/delegate, CancelTaskDialog/cancel route, run-step/host containment, read-only SQL and public detail/recall/tree/list |
| Terminal-unchanged and command ledger | Actual P and identified completed-baseline cancel commands exit1 with exact409 and human message; full relevant content unchanged | Duplicate audit/timestamp/result mutation or wrong guard | Callback replay and Pause refusals are different routes | Shipping `happyranch cancel` and public cancel route |
| Delayed CLI, provider/relay/socket fixtures and unchanged window | Original real W POST forwarded after cancel/closure; exact409; full1s fresh observations, <=50ms interval, no extra admission/result/consumption/revisit/peer | Copied SID masquerades as issuer, pre-forward leak, wrong auth denial, late resume or result | Existing committed-response loss delays a response, not an issued request | Genuine executable provider/CLI, loopback HTTP, task-active fence, host metrics and coherent SQL |
| Prefix/export/aggregate consumers | Exact53 prefix, genuine E05 byte/record offset, entire suffix classification; raw/observations/scenario/result agree; independent actual-content recomputation | Dropped evidence field, count-only PASS, accepted unaccounted callback, truncated prefix | Former aggregate assumed final37 and whole E05 witness equality | Existing five daily artifacts and source-bound independent aggregate |
| Sanitation/partial process records | Retained bearer and E05 contexts redact whole captures before clipping; unknown context/caps refuse full-content claims; launch evidence survives client death | Secret fragments on timeout/EOF/signal or fabricated posthumous return | Prior synchronous success receipt cannot describe a killed CLI | Private launch receipt, real Popen/socket streams and existing unsafe-export refusal |

R1–R5 correction contracts (TASK10778):

| Changed assertion/fixture/consumer | Contract | Credible defect | Previous coverage gap | Actual seam |
| --- | --- | --- | --- | --- |
| E05 aggregate transport | Original daemon/CLI/browser D/L/P keys and registered ports only; independent E07 listener/upstream predicate stays in E07 | Valid E05 fails on nonexistent E07 keys | Misplaced predicate disagreed with unchanged E05 producer | `threads.exercise` serialized transport |
| Browser cancel and independent aggregate | Full literal `{ok:true, task_id:P, cancelled:[P,W], killed:[{task_id:W,agent:case_worker}]}` with exact order and membership | Reject real successful200 or accept incomplete response | Prior literal omitted ok/task_id | Shipping `tasks.cancel_task` and actual dialog POST |
| Both new stub witnesses | Closed seven-field records, exact Claude/source/stub/plan hashes, integer argc7 and ordered flags `[-p,--output-format]` | Arbitrary provider/hash/argc/flag or unknown-field corruption passes internal copy equality | Suffix counted kinds/source only | Task templates select fresh unpinned Claude; `ClaudeAdapter` seven-argument launch, original `fake_claude.sh` argv and unchanged executable guard |
| E07 ownership updates | Atomic temporary/replace union retains all earlier PIDs; register before process observations/waits | Watcher reads truncated JSON or failure loses an observed ancestor | New direct overwrites differed from atomic `Controller.start` | Existing `Owned.sample/observe` with unchanged PID-start/cgroup/unit checks |
| Private launch writer and final collector | mode0600 temporary creation/replace, initial PID record retained until full acknowledgement commits; each issued command requires a record; retain valid siblings and valid initial records with fixed missing/malformed/incomplete/unexpected diagnostics | Interrupted write destroys all later evidence or glob hides missing record | Unguarded comprehension and truncate/chmod publication | Genuine provider Popen and acknowledgement, `Controller.evidence`, existing run e07 whitelist/export, independent aggregate |

Launch collection parses each expected command-bound file independently. Failure
categories contain only fixed words and a command index; raw malformed bytes,
filenames and exception text are not exported. Valid partial records and the
failure list travel inside `e07` through raw/observations/scenario/result. Other
evidence is serialized before collection failures raise; aggregate rejects any
failure and independently requires both complete records in command order.
Sanitation remains outside parse catches: unavailable retained context still
refuses export, and incomplete records never fabricate an acknowledgement.
These source corrections add no dedicated control or execution evidence.

All dedicated sensitivity controls remain **UNAUTHORED**; behavior, assertion
sensitivity, transport provenance, delivery/persistence, fit, process closure
and sanitation are **MISSING/UNEXECUTED**. No full test-authoring PASS. Protected12
mutations/RED-restoration-GREEN/repetitions remain unauthorized/UNEXECUTED.
General and actual callback integration remain SKIPPED under THR243seq42.

Exact cumulative source expectations:39 starts=A12+B27,23 tasks/32 results
(alpha22/31,beta1/1),21 completed/two cancelled; only P adds an accepted result.
Eight jobs/seven scripts and every E05 thread/invocation/delivery count remain.
B27/26/26/active1 becomes settled B27, with P success then W cancellation and
full duplicate-preserving B25/B26 tails; queued-cancel delta0. E04 retains28
anchors and its command ledger, E05 retains starts30:37 and its original
observer/witness boundaries. E07 commands/anchors are separate.

C partitions16+12+8+6+8=50 use fixed absolute ends clipped by original C/scenario
ends. The shared scenario remains min(entry+480,original+720), other commands
and interruptions240, cleanup60/export120, hard900/target600/hosted15min.
375+50=425 is unmeasured arithmetic. No borrowed reserve or shortened1s window.
Daily19UTC/default-branch Ubuntu Python3.12/3.13/3.14 and TERM3.12/INT3.14,
five seven-day artifacts and lint-only merge gates remain unchanged.

Source-observed successful live cancel-CLI printing mismatch (`killed` has
agent/task_id but cmd_cancel reads pid) remains unresolved and unexecuted;
positive cancellation uses the shipping browser. Retained CLI terminal-refusal
branches do not reach that print. E04 output-cap/persistent-stop, R/full
unchanged-bound disposition and all daily behavioral acceptance remain with
root TASK10680. TASK10555 retains first-daily/Pause acceptance; first daily is
PENDING per owner. Authored, independently reviewed, merged, executed and
accepted are separate milestones; this is not completion of the reset.

## E07 R authored suffix — TASK-10787

The accepted TASK-10782 amendment
`83d892a3e6eae329879ebf7be7a9dca47854f15f6bbe67be3811c4820e1fe184`
extends the original E07 design with exactly
`E07.abrupt-dead-session-recovery` and `E07.restart-late-cli`.
Earlier C-only/held-R statements above describe that delivery boundary. The
current source expects **58 variants, 40 starts=A12+B28+C0, 24 tasks/32 results**
(alpha23/31, beta1/1), 21 completed/two cancelled/one failed. Only **39 product
receipts=A12+B27** exist. The sole B28 active invocation dies with boot B;
fixture signals/reaping and new C0 cannot manufacture its terminal receipt.
These are authored expectations, not observed execution counts.

`D=min(original scenarios entry+480, original run start+720)` remains fixed.
Constructor, setup, original53 and full pre-C archival finish strictly before
D−150. C retains its full50 with16/28/36/42/50 ends; R retains75 with
10/15/25/50/60/65/75 ends; normal close/evidence receives a separate25.
Admission requires the complete downstream reservation. Positive explicit
socket, child, SQLite and Playwright bounds use the smaller effective end;
return checks and the controller alarm cover operations without native timeout
arguments. Fixed31s cache, both Pause windows, and full C/R1s observation windows
are unchanged. Outer cleanup60/export120/hard900/target600/hosted15min stay
separate. The estimated355 prefix versus330 ceiling remains **UNPROVEN**;
late/missing stages fail the full manifest. No measured fit or universal
interruptibility of OS/library calls is claimed.

The shared response helper retains the main page's actual stored timeout:
initial15s, E04 `job_open` at most10s, E07 `e07_detail` at most4s. An already
clipped page default stays clipped until the next explicit transition. This
also applies to inherited `open_task`, completion/cache revisits and bilingual
Pause actions. E05's separate thread page retains its independent10s default.
The response context fixes its operation end at entry, clips the native timer,
checks immediately before the context exit can wait, and rejects late returns.
Provider release reads the caller's existing socket cap (E05 at most5s;
other callers retain their start/command cap), clips write/flush/read/close to
that cap and the same effective phase end, and checks each return. Exact exit
acknowledgement, process/private-file/CLI-child closure and evidence joins remain.
No timeout metadata or serialized evidence shape is renamed to mask a wider cap.

R creates one independent ordinary root through public API and joins its real
case_manager provider to a provider-authored CLI payload, wrapper witness,
PID/start/ancestry, accepted socket tuple/inode and full captured request.
`e07-prefix.json` keeps original53/37; `e07-c-prefix.json` freezes actual56/39;
`e07-restart.json` carries the distinct R record; `e07-predeath.json` freezes
its complete archive before the signal. Complete ordered,
duplicate-bearing A12/B27 arrays survive. Two literal B28/27/27/active1 samples
and full SQL/public content precede exact daemon pidfd SIGKILL and actual
Popen−9. An R-only runner-subreaper request authenticates the previously owned
process set, signals by identity, and uses targeted waitpid(PID,WNOHANG).
ECHILD records another parent; it is never a claimed reap. Numeric PID reuse
fails closed. C starts once only after numeric executor absence and closed
old tree/unit/listener evidence, over the same paths/inodes/source/registration.

Shipping recovery must produce literal failed/note
`session died on daemon restart -- executor pid not alive`, preserved historical
SID/PID/step1, NULL completed_at/cancelled_at, exactly one R restart audit and
no result/relaunch. Real public/browser readback includes disabled Cancel and
settled task/thread history. The bearer persists. Only after valid C readback
does the original held request forward once:409 task_not_active/failed/
cancelled:false, distinct dead-downstream evidence, then a full1s unchanged
window at≤50ms sample cadence. Two genuine empty C0 samples and normal daemon
exit0/controller close are independently required. Outer rescue cannot supply
normal-close success.

Every generation and discovered half-ready credential/port/process remains in
the cumulative ownership and private redaction context. Partial launch records
retain valid siblings; unknown context refuses unsafe raw export. Independent
aggregate consumers reopen actual archive/raw/observation/result content and
check exact boundaries, source hashes, request/response/ownership joins,
recovery field deltas, receipt tails and time ordering. No production seam,
schema, authorization or permission change is introduced.

| Changed group | Contract and public action | Credible regression / described control | Existing gap | Actual seam and level |
| --- | --- | --- | --- | --- |
| Shared deadlines | Complete original prefix, C, R and normal close inside original D | Nested fresh timeout or late stage appears successful; a future delayed-readiness/close control must fail with partial evidence | C-only deadline did not reserve R/normal close | run → Controller/poll/API/Store/browser/provider/transport; harness lifecycle and real CLI/browser |
| Named crash gap | Genuine held CLI, full B28/27/27/1, actual−9 and closed old tree | Drop/reorder an interior duplicate receipt, add a start or invent B28 release; future serialized controls must refuse | C ends settled B27 | public create/provider/CLI → HostSessionStore evidence → Owned targeted close → independent aggregate; system evidence |
| Dead non-NULL recovery | One same-files startup yields literal failed without result/relaunch | Start before numeric death, omit sweep, or insert a result; future controls must reject field/content joins | Drained Pause restart has no live no-result task | shipping startup sweep/update_task, fresh read-only SQL/public/browser; full system |
| Old request denial | Original captured request reaches recovered failed-task409 then full1s unchanged | Reconstruct body, forward early, expect401, or shorten window; future transport controls must reject | C only covers cancelled W | provider_command → LateTransport → shipping task-active gate; genuine CLI/HTTP/browser |

No dedicated sensitivity controls were authored or run. All new behavioral,
sensitivity, provenance, transport, delivery, persistence, closure, sanitation,
and successful-fit proof is **MISSING/UNEXECUTED**. Protected12,
RED/restoration/GREEN and repetitions remain unauthorized/UNEXECUTED. General
and actual CLI callback integration remain SKIPPED under THR243seq42; retired
Python units stay retired. Static lint is not full test-authoring or behavioral
PASS. First daily remains PENDING per owner. E04 output-cap/persistent-stop,
other recovery branches, the separate live cancel-CLI mismatch and overall
program acceptance remain root-owned. TASK10555/JOB4257 and the daily19UTC
matrix/artifacts/triggers/retry/notification policy are unchanged.
