# Python test reset — THR-291 seq40

Founder seq40 authorizes deletion of the entire former Python unit selection
before replacement. It supersedes selective retirement, replacement-before-deletion
and a small retained contract tier. This is accepted loss of test source, not
compatibility proof. Git history retains the old source; no hidden copy or E2E
rename is maintained. Product runtime/CLI code, schema, auth, permissions and
behavioral predicates are unchanged.

## Source inventory and retained boundaries

At base `529a085dc09c1ca1aacf7cb563be9f2c37ed0621`, tracked source AST,
pytest configuration, conftests, module/class/function markers and marker aliases
resolve 9,994 test definitions: **9,617 deleted unit definitions**, **155 retained
integration definitions**, and **222 retained canonical platform definitions**.
These are static class/function definitions, not expanded pytest items, executed
results or coverage percentages. Parameter decorator expressions are inventoried
without execution. Three test-named fixture definitions are not cases.

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
symbols under `output/TASK-10478/`. Static extraction imports no old tests and
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

Required active PR and exact-merge main checks: Web (Node 24), Linux Canonical
Store Validation (Ubuntu), macOS Canonical Store Validation (macOS 15), plus
applicable Docs and other retained path-specific checks. The Linux canonical
job still requires the explicit existing real callback smoke. Units are RETIRED,
not skipped-pass; new product E2E is **PENDING**, not implemented or PASS.

Bare/default/directory Python selection refuses before test-module collection.
The existing explicit platform files and isolated-parent `-m integration` entry
remain. `local_ci.sh python` exits 2; `all` verifies retained Web checks only.
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
  THR297's 40 interpreter setup errors, three stale `_FakeOrch.attach_memory_collection`
  failures and 28 systemd gaps remain separate. Helpers/schedule fixtures stay;
  no interpreter repair, systemd provisioning, Jenkins mutation/allocation/run
  or real provider inference is part of this change.
- Historical reports and old spec test recipes do not authorize old-unit runs.
  New required-check enforcement, genuine compatibility coverage and native/
  signed/tailnet or authentic population evidence require actual receipts.

## Parent-owned continuation and external parity

TASK-10476 owns merge/closeout and the freshly authored isolated browser → daemon
→ executable provider stub → actual CLI callback → independent durable/audit
readback harness. First cases cover task completion, identical and lost-response
replay, changed-payload conflict, known-task stale/fabricated-session denial,
missing/wrong bearer, colliding IDs across two orgs, and UI org switching.
Jobs, threads, cancellation/late callback and actual daemon restart/recovery
follow. None is claimed delivered here. Proposed future Python E2E matrices are
PR 3.12/3.14 and main/release 3.12/3.13/3.14; they are not installed checks.

Parent must coordinate stale `engineering-team-workflow` unit-always wording,
local-CI/test-authoring KB entries, engineering charters and delivered skill
refresh. Only release-owned bundled skill sources change in this PR. The
`integration-agentic-cli-stub-only` KB remains binding. Historical implementation
reports retain their original failures, counts and allocations.
