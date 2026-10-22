# Python test reset — THR-291 seq40

Founder seq40 authorizes deletion of the entire former Python unit selection
before replacement. It supersedes selective retirement, replacement-before-deletion
and a small retained contract tier. This is accepted loss of test source, not
compatibility proof. Git history retains the old source; no hidden copy or E2E
rename is maintained. Product runtime/CLI code, schema, auth, permissions and
behavioral predicates are unchanged.

## Source inventory and retained boundaries

At fresh base `ea559208b3f87ce3c3b17bd3b645dfafa4eef41b`, tracked source AST,
pytest configuration, conftests, module/class/function markers and marker aliases
resolve 10,003 test definitions: **9,626 deleted unit definitions**, **155 retained
integration definitions**, and **222 retained canonical platform definitions**.
These are static class/function definitions, not expanded pytest items, executed
results or coverage percentages. Parameter decorator expressions are inventoried
without execution. Three test-named fixture definitions are not cases.
The original base `529a085dc09c1ca1aacf7cb563be9f2c37ed0621` receipt of
9,617 removed definitions and 472 deleted files remains historical. PR1010 adds
nine unit definitions in eight already-deleted files; all are retired in this
merge-forward. The 472-file deletion set and retained definition counts are
unchanged. Upstream product, Web, shared OpenAPI and documentation changes remain.

There are three mixed files: `tests/integration/test_nested_daemon_environment.py`
and `tests/platform/test_{linux_systemd,macos_process_group}_backend.py`. The latter
two use `real_integration = pytest.mark.integration`; their 19 marked definitions
survive. All unit definitions in mixed files are removed. Module-wide integration
markers outside the integration directory also survive. The seven canonical
files listed in [Local CI](local-ci.md) survive solely as the existing platform
lane, despite their former overlap with units. This exception admits no other
unmarked tests.

The maker exports exhaustive before/after case identities, parameter expressions,
file deletions, marker overlap, fixtures, imports, resource consumers and changed
symbols under `output/TASK-10522/`; `output/TASK-10478/` preserves the historical
inventory. Static extraction imports no old tests and
runs no pytest collection. Independent review/QA bind that evidence to the final
head. Removal alone is not a passing test result.

Shared integration parent/stub guards, executable provider plans, two-org capture,
Assistant artifact/native observer helpers and schedule fixtures are retained.
`tests/helpers/task_scratch.py` extracts only `_proc` and `_snapshot` from the
retired scratch-report test module; retained containment cases import them there.
Remote DIY integration retains its actual policy-envelope helpers. Unit-only
fixtures, helpers and archived test data are removed.

Web Vitest and native Swift/Go/platform suites and product assets remain.
`tests/contract/openapi.json` and `route-classification.json` retain their Web and
Swift consumers; `scripts/generate_openapi_snapshot.py` remains the maintainer.
The hostile lab retains the four JSON inputs read by its actual `Contract` reader:
route policy, credential taxonomy, failure categories and threat cases. Its real
execution remains separate from the deleted Python preflight. The old unused
lifecycle/topology fixture copies are removed; normative product requirements
remain in the managed remote access spec.

## Gate state

Founder THR-291 seq69 makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq247 additionally authorizes **ONE** immediate full main execution under
sole root TASK-11897 through the reviewed `workflow_dispatch` entry. Manual
admission requires actual `refs/heads/main`, default branch `main`, and matching
repository/workflow/run/source identity. This entry is not standing execution
authority. Baseline56 variants EACH Python3.12/3.14 plus TERM3.12/INT3.14 retain
exactly four execution artifacts and the original bounds. `Daily Product E2E
Health` remains outside merge checks and reports the actual event, run/attempt,
SHA and checkout tree; manual receipts are never labeled scheduled.
There is no local E2E alias, PR/push/release/workflow_call/repository_dispatch
entry, automatic retry, extra schedule or SLA. Admission, lint and scoped QA
success do not establish product execution success. The root owns the one
actual dispatch after independent review/scoped QA, both final-head lints,
guarded merge and an exact-main receipt. Daily-only statements in the historical
case-stage records below describe their original source, not current admission.
Scheduled historical runs38482572306 and38644465643 were observed and failed
at their own immutable sources. Corrected behavior remains **PENDING** until
an authorized main run executes the repair; historical3.13
failures and all-six-cell evidence remain unchanged. Prior first-slice receipts remain
historical evidence at their own source, not daily/final-head verification.
The bounded launcher uses one baseline-scenario cap480 (other five commands
and cancellation scenarios240), clipped by original start+720 and shared by
controller/watchdog. Cleanup at most60 includes owned process/unit/group,
port/socket/data closure and sanitized evidence staging, clipped by original
start+780. Export at most120 from its actual entry is clipped by original
start+900, retains a bounded incomplete FAIL receipt and grants no extra teardown
or action time. Hosted upload shares the remaining900s/15-minute envelope;
local timers do not guarantee upload success. Timing evidence is required by
both daily consumers. These source bounds and480−375=105 nominal planning slack
are not measured fit or observed signal/cleanup/export behavior. The42 baseline/Pause variants remain at their original boundaries;
the E04 prefix below adds six. Cadence and the600 target remain unchanged. See [the timing contract](../e2e/README.md).
Engineering_manager owns daily failure/missing-evidence triage through existing
tasks/threads; no new notifications or guaranteed response SLA is implied.

Python units stay RETIRED; `python` exits2. General integration is **SKIPPED**
under THR-243 seq42, never PASS. Web Vitest, native/platform, integration,
the named actual CLI callback and their isolation fences retain their sources.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E. Legacy nightly integration remains separate
at 07:00 UTC, with its historical failures and fixtures unchanged; its manual
`all_only` input runs the same lint/static all selection. No legacy repair or
run is authorized by this cadence change.

Bare/default/directory Python selection refuses before test-module collection.
The existing explicit platform files and isolated-parent `-m integration` entry
remain. `local_ci.sh python` exits 2; `all` verifies source boundary and Web lint/static checks only.
The obsolete PR1011 full-unit path, fixed G unit inventories/controls/repetitions
and lab unit preflight are deleted. Disposable manual-all provenance, bounded
capture and environment infrastructure remain.

The waiter takes explicit expected checks and verifies their completion at the
pinned head; it has no hard-coded obsolete Python requirement. Source changes do
not configure GitHub branch protection or prove required-check enforcement.
Parent owns that verification and any stale external check configuration.
Review/QA independence, exact-head evidence renewal, normal hooks and guarded
merge remain mandatory. Every push renews review/QA. No deployment is included.

## Uncovered product-risk requirements

- All deleted parser, component, contract, lifecycle, rollback, permission,
  schema/migration, compatibility and mocked-boundary obligations remain product
  risks until fresh proof exists. A small happy journey cannot close this set.
- The six C4/D4/KB-view compatibility cases, both fresh import orders, the
  587-member facade and both memory API contracts remain explicitly uncovered.
  PR1014's three-test correction is superseded source work when deletion merges;
  it is not merged, closed or credited as PASS by this change. Parent coordinates
  its disposition.
- Authentic memory failure remains **UNKNOWN**. The 499/500 ordinary-session,
  14-complete-day, 29/30-agent, exact 10%, strict-majority and distinct-search/get-pair
  predicates are unchanged and unverified by this deletion or small E2E journeys.
- General integration remains **SKIPPED** under THR-243 seq42, never PASS.
  THR297 archived Build11 FAILURE (143pass/3fail/40errors/28UNTESTED),
  with historical two-org cause UNKNOWN. Its 40 interpreter setup errors, three stale `_FakeOrch.attach_memory_collection`
  failures and 28 systemd gaps remain separate. Helpers/schedule fixtures stay;
  no interpreter repair, systemd provisioning, Jenkins mutation/allocation/run
  or real provider inference is part of this change.
- Historical reports and old spec test recipes do not authorize old-unit runs.
  New required-check enforcement, genuine compatibility coverage and native/
  signed/tailnet or authentic population evidence require actual receipts.

## Parent-owned continuation and external parity

TASK-10579 owns cadence delivery/merge/closeout, preserving terminal TASK-10476
history and the freshly authored isolated browser → daemon
→ executable provider stub → actual CLI callback → independent durable/audit
readback harness. First cases cover task completion, identical and lost-response
replay, changed-payload retention under an ordinary static binding, known-task stale/fabricated-session denial,
missing/wrong bearer, colliding IDs across two orgs, and UI org switching.
Jobs, threads, cancellation/late callback and actual daemon restart/recovery
follow. The first eight cases are implemented in the separately authorized
standalone lane; [its case record](../e2e/README.md) distinguishes source from
actual execution/authoring evidence. The daily-only matrix is 3.12/3.14; historical scheduled runs38482572306/38644465643 failed; corrected behavior is pending. The aggregate requires every cell/variant, exact
source, zero skips and cleanup; no installed ruleset enforcement is inferred.
Public API creates ordinary tasks; the built browser verifies lifecycle and
alpha-beta-alpha isolation. No ordinary-task creation UI is added or covered.

Parent must coordinate stale `engineering-team-workflow` unit-always wording,
local-CI/test-authoring KB entries, engineering charters and delivered skill
refresh. Only release-owned bundled skill sources change in this PR. The
`integration-agentic-cli-stub-only` KB remains binding. Historical implementation
reports retain their original failures, counts and allocations.

### E04 daily source prefix

The daily harness appends six E04 variants after23 baseline and19 Pause
variants (48 authored). Founder THR291seq301(b)/303 replaces only original slot44
with E04.review-required-refused under sole ROOT12445, accepted R1–R3 after
independent TASK12447 design review. No legacy fixture or parity keeper: that
variant's individual rejection and natural waiter wake are expressly retired.
Five automatic-job journeys retain genuine submit/block/read/final callbacks,
completed0/7, timeout/-9 and pair terminal orders. The refusal uses one live
ordinary task/SID, exact local CLI exit2/empty stdout/shipping stderr/no HTTP,
then a separate identical-entity authenticated public422 with exact detail.
Selected Controller.api capture preserves raw bytes, source/boot/loopback peer
and intervals; coherent both-org read-only frames, full product output bytes,
owned descendants, persistent sentinel/socket absence and the preceding actual
automatic0 positive control establish no allocation/effects. Only exact current
task's joint strictly forward aware heartbeat+updated_at movement is allowed.
No refusal audit allowance. Original SID completes once with genuine INTEGER
result, ordered step1 lifecycle and truthful task/list/Recall screenshot.
BootA12 stays archived; E04 closes bootB17/cumulative29, alpha19tasks28results/
beta1/1, seven jobs/seven scripts/zero rejected,27 anchors and54 provider CLI
commands (eight submit attempts including refusal, five blocks, six finals,
35 reads). Per-case20/15/20/25/25/25 bounds stay. Admitted-job reads still bind
shipping method/URL/query/HTTP200 and durable content, including live S2's
historical blocked S1 result. CLI child deadline remains caller minus one second.
Partial failure evidence uses existing sanitation; no production seam follows.

The [E04 case/control records](../e2e/README.md#e04-jobs-prefix-task-10686--thr-291)
carry all four authoring answers and consumer mapping. Source/static checks do
not establish execution, assertion sensitivity, fit, sanitation or cleanup.
All new behavior/proof remains MISSING/UNEXECUTED; corrected daily behavior pending after observed historical failures.
E05/C are authored source with execution limits; abrupt R remains HELD. No entire-program PASS.
The480 baseline/240 other commands/720 cutoff/60 cleanup/120 export/900 hard
bounds and19UTC daily cadence remain; no extra trigger/build/test merge
lane, protected proof, collector action, Jenkins or deployment is authorized.

### E05 authored threads/replay increment

The accepted four-case/five-variant E05 source adds a real browser compose race,
genuine finite reply closure, one provider-owned self-dispatch/task/followup,
same-live-provider consumed-token409 replay, and genuine task-session403 send
to a worker-only denial venue. The53-variant union preserves all48 earlier
variants. Expected cumulative providers36 comprise archived bootA12 plus current
bootB24; final global21tasks30results and six thread invocations are distinct.
Actual-content consumers check coherent negative frames, literal timelines,
source/CLI/provider identity, ordered receipt tails and proxy/browser closure.
The full authoring map and source-only limits are in `e2e/README.md`.

No behavioral or complete test-authoring PASS follows. E05 assertions, proxy
compatibility, same-tick guard sensitivity, sanitation, persistence/delivery,
closure and fit remain MISSING/UNEXECUTED. Protected12 deliberate defects and
RED/restoration/GREEN/repetition proofs remain unauthorized/unexecuted. Python
units remain RETIRED; general integration remains SKIPPED. Daily-only schedule,
bounds, matrix, artifacts and root-owned collection obligations remain.
Cancellation/late-callback/restart increments and daily acceptance remain open.

E05 PR1040 R1–R3 source correction binds every named CLI receipt to its
canonical command/instruction and actual public outcome, derives hold/navigation
from the complete browser ledger with literal per-message rendering joins, and
redacts full captured streams with retained credentials before byte clipping.
The48/53 variant counts remain; Founder303 amends provider counts to A12+B24=36. Dedicated
contradictory-artifact and sanitation controls are UNAUTHORED; all behavior,
sensitivity, proxy/guard, sanitation, delivery/persistence, closure, fit and
protected proof remain MISSING/UNEXECUTED. This is static source correction only;
the existing daily-only policy and all execution holds remain in effect.

### E07 C authored cancellation increment

The C-only source appends three variants to the preserved53:56 authored
variants,38 starts=A12+B26,23 tasks/31 results (alpha22/30,beta1/1),21 completed
and two cancelled. Real browser cascade and terminal CLI refusals precede
forwarding W's original captured provider CLI request; independent serialized
content, process/socket provenance and a full1s unchanged window are required.
The E05 witness prefix is bound to its actual byte/record offset, with every
E07 suffix record accounted. [Case and four-answer records](../e2e/README.md#e07-c-source-increment--task-10773)
retain all missing proof. R/full remains HELD, with no new restart/rotation code.
C50 uses fixed16/12/8/6/8 partitions within unchanged480/original720;425 is
unmeasured arithmetic. Daily-only19UTC and the four execution artifacts are unchanged.
Source-only authoring is not independent review, merge, execution or acceptance.
All behavior/sensitivity/provenance/fit/closure/sanitation proof is UNEXECUTED;
dedicated controls UNAUTHORED; protected12 and repetitions unauthorized.
General/actual callback integration SKIPPED THR243seq42; retired units untouched.
The live cancel-CLI print mismatch and E04 output-cap/persistent-stop remain
unresolved. Sole ROOT TASK12445 resolves superseded TASK12281 and owns main delivery/execution
and collection, preserving all historical failures and unknown native ownership. No entire-reset or test-authoring PASS.
