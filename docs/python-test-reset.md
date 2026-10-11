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

Fresh Product E2E runs **schedule only**, `0 19 * * *` (19:00 UTC / 03:00
Asia/Shanghai), on default-branch immutable `github.sha`. Baseline Python
3.12/3.13/3.14 and existing TERM3.12/INT3.14 launcher diagnostics feed
`Daily Product E2E Health`. It is never a merge check. No local E2E alias,
manual/PR/push/release dispatch, auto-retry or extra schedule is supported.
First scheduled execution is **PENDING**; prior first-slice receipts remain
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
actual execution/authoring evidence. The daily-only matrix is 3.12/3.13/3.14; first scheduled run is PENDING. The aggregate requires every cell/variant, exact
source, zero skips and cleanup; no installed ruleset enforcement is inferred.
Public API creates ordinary tasks; the built browser verifies lifecycle and
alpha-beta-alpha isolation. No ordinary-task creation UI is added or covered.

Parent must coordinate stale `engineering-team-workflow` unit-always wording,
local-CI/test-authoring KB entries, engineering charters and delivered skill
refresh. Only release-owned bundled skill sources change in this PR. The
`integration-agentic-cli-stub-only` KB remains binding. Historical implementation
reports retain their original failures, counts and allocations.

### E04 daily source prefix

The daily harness appends six accepted E04 jobs variants after the unchanged
23 baseline and19 Pause variants (48 authored). Real provider-owned submit/
block/read/final CLI callbacks, browser approval/rejection, completed0/7,
failed-timeout/-9 and both two-job terminal orders retain literal durable and
owned process oracles. BootA12 is archived across the existing Pause restart;
bootB closes18, cumulative30 providers, alpha19tasks29results/beta1/1, eight
jobs/seven script starts. Actual-content aggregate validation reads observation
and browser artifacts and rechecks serial receipt windows and outcome joins.
Each jobs read is joined to its shipping method, job URL/query, HTTP200 and
actual response/CLI content against the durable row. A source-timed live-S2
snapshot must retain exactly the blocked S1 result before reads/final callback;
human show must match the exact first ID/status/timestamp line. CLI subprocess
expiry precedes the original bounded caller deadline by one second to deliver
partial timeout evidence through the existing sanitized exporter. These source
corrections have no behavioral, persistence, sanitation or fit proof yet.

The [E04 case/control records](../e2e/README.md#e04-jobs-prefix-task-10686--thr-291)
carry all four authoring answers and consumer mapping. Source/static checks do
not establish execution, assertion sensitivity, fit, sanitation or cleanup.
All new behavior/proof remains MISSING/UNEXECUTED; first daily PENDING per owner.
E05 and later cancellation/restart remain future work; no entire-program PASS.
The480 baseline/240 other commands/720 cutoff/60 cleanup/120 export/900 hard
bounds and19UTC daily-only cadence remain; no extra trigger/build/test merge
lane, protected proof, collector action, Jenkins or deployment is authorized.
