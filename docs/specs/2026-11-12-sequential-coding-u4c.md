# Sequential Coding U4c: selected Developer repository transport

Current B1 addendum: @2 discovery/compose available; public admission unavailable
(`workflow_start_v2_unavailable`). This supersedes older public-@2-closed
statements about discovery/composition only. @1 bytes remain frozen; no admission,
continuation/recovery or Draft→Review registration is delivered by B1.
[Current B1 contract](../agent-guides/contracts/reusable-workflows.md#r2-unit-b-b1-read-only-start2).

Current R2 unit (a) addendum: additive Coding v3/@2 setup reads/Save and separate
Founder Allow/Disable are supported in source. Save preserves admission; Disable
affects new starts only; immutable admitted pins and @1 contracts are retained.
Public Start@2 admission and Draft→Review registration remain undelivered. Registration
still creates no defaults or admission; offline fresh admission remains refused.
Live setup/Allow/deploy/restart/pilot are not performed by source delivery.
Exact routes, custody and supported fixture commands:
[R2 setup/admission contract](../agent-guides/contracts/reusable-workflows.md#r2-unit-a-saved-defaults-and-new-run-admission).

## S8b registration and transport boundary (THR-336)

Coding v3 is the generic Coding definition in `GENERIC_CATALOG` (the compatible
`STAGED_GENERIC` registrar export retains the same identity/pins). Registration
adds immutable definitions only. `PRESET_CATALOG`, public `MANIFEST`, the
`workflow-sequential-start-context@1` wire shape and Start@1 composer stay
unchanged, offering v2 only. A fresh public v3 pin still refuses with
`sequential_release_pin_mismatch`. B1 provides @2 discovery/compose; public admission remains unavailable. V2 fresh-start behavior is unchanged.
Missing settings remain Disabled/unset; registration creates no settings, roles,
admission, tasks, runs or Allow. Explicit Founder setup/Allow is separate above.

Fresh @2 selected runs refuse repository-resolve/bind/preflight and sequential
Final transport with `sequential_domain_transport_unavailable`. Local Final
selection/journal guards also refuse the registered generic pin before effects.
Original @1 selections, admitted replay, selected-history reads and uncertain
Final journal bytes retain their original contracts; saved records never grant
reissue authority. Offline `setup_sequential_coding.py register` selects v3 only.
Fresh offline admission refuses `sequential_coding_registration_only`; original
@1 admission-show and exact command/receipt replay remain read-only.

V3 repository/base/worktree preflight, independent exact-head review, same-head
QA and guarded non-admin expected-head merge/remote observation are agent duties,
not engine capabilities. The canonical responsibilities and generic JSON report
convention are in `docs/agent-guides/contracts/coding-workflow.md`, “Coding v3 role
reports”. Generic completion accepts a settled attributed report and does not
independently verify repository heads or merge truth. The historical special
repository/finalize adapters, typed Coding outputs and old guarantees in this
document apply only to their actual v1/v2 or legacy binding; v3 does not inherit
them. Original bytes, pins, replay and uncertain effects remain unchanged.
Semantic role adherence/pilot is founder-reserved and NOT RUN; no live operation
is authorized by this source delivery.

Status: current source contract. ROOT TASK-15606 / THR-336 replay ruling,
2026-11-12; supersedes U4/U7 unavailable subject-binding statements only.
Source delivery is not genuine Coding/V3 acceptance or deployment.

## U4c current selected-Developer repository transport (TASK-15660)

The earlier U4 checkpoint's unavailable public subject-binding adapter is
superseded by this specification.
`happyranch workflows sequential repository-resolve` and `repository-bind`
require `--org ORG --session-id CURRENT --from-file /absolute/command.json --json`.
The canonical closed command contains exactly `format` with value
`workflow-sequential-repository-command@1`, `repo_key`, `base`, and `scope`.
There is no operation key or response cache. The existing 48KiB command bound
and repository service limits apply without a new allowance.

Both POSTs reuse U7c1's own-current ordinary task-session route boundary. The
server derives task/principal, then a SELECT-only adapter gate checks ownership
and the current selected Developer code-change visit before either service call.
Non-selected ordinary/non-sequential, browser/bearer, foreign, ended, recovery
and thread callers refuse with zero INSERT/UPDATE/DELETE and zero COMMITs.
Existing refusal codes remain: 403 audience/session, 404 absent task, and 409
sequential_carrier_identity_conflict for ordinary non-visit ownership or
sequential_repository_session_unavailable for unavailable Developer selection.
Native browser audience is explicitly excluded; no TS mirror applies. Current
session checks bookend off-loop service execution. The service's unchanged
selected/native checks remain authoritative: selection lost after the gate can
still refuse with the pre-existing transient lease. This residual window is
accepted; no atomic racing-session or filesystem fence is added.

Resolve returns a fresh current-authority receipt on each call and persists no
repository/subject/hold/evidence/business rows. The coordinator's transient
publication lease is acquired and released; successful resolve is not zero total
writes. It never binds or releases editing. Bind forwards existing behavior:
exact/narrower requests reuse the original immutable subject; drift returns a
durable Held, preserving original subject/hold bytes. An identical request
after origin drift yields `repository_changed`, which takes precedence over
original success. Broader supported scope yields the existing
`sequential_repository_subject_changed` Held; subsequent changed requests may
refuse `sequential_repository_hold_conflict`. No body-conflict policy is added.
The CLI prints the original service JSON, exits 2 on Held/refusal/uncertainty,
and never retries automatically. After transport loss the Developer may
explicitly reissue the same canonical body; current bind semantics still win.
Only positive subject COMMIT releases editing; failed COMMIT releases nothing.
U7c1 GET `repository-preflight` remains a read of the already-bound subject.
No selected-context, observer, schema, credential, permission generation,
network/effect, historical evidence or live deployment change follows.

## Route and byte contract

POST `/api/v1/orgs/{slug}/workflows/sequential/repository-resolve` and
POST `/api/v1/orgs/{slug}/workflows/sequential/repository-bind` accept exactly
one query pair `session_id`, CLI surface header, and the canonical UTF8 command.
No supplied task/principal/visit, duplicate or extra JSON keys, whitespace suffix,
noncanonical ordering, extra query, bearer or browser Origin is accepted.
The command adds only `format` to existing RepositoryPreflightRequest fields.
Schema bounds on repo_key/base/scope remain; service decides supported scope.

Successful HTTP200 carries existing canonical preflight@1 receipt bytes or
repository-hold@1 bytes, with Cache-Control no-store. A resolved resolve receipt
is observation only; a resolved bind receipt follows positive service COMMIT.
HTTP403 is audience/current session/authority denial, 404 absent native task,
409 service/selection/authority/history refusal or failed COMMIT, and 422
query/body syntax or size refusal. No automatic transport retry is installed.
No operation ledger, new persistence, new cap, second CLI OrgState, daemon
GitHub call or new credential/allow rule exists.

Native SurfaceAllowList explicitly denies both POSTs; managed connector remains
default-denied. AGENTS requires TS mirrors only for browser-callable routes,
so none applies. The retired openapi-coverage.test.ts/Vitest is not restored.
OpenAPI and route classification change in the same PR.

## Evidence limits

Out-of-repo synthetic fixtures drive actual HTTP handlers, parser/CLI, service,
selected/complete readers and local Git observation. Authority and native host
ownership scaffolding is synthetic. No live task allocation, genuine native
workflow, V3, effect recovery or production mutation is represented. Existing
bound subjects and Holds retain their original bytes; no migration or rebind.
Independent full review, scoped same-head QA, both required lints and manager
guarded merge remain. Product E2E stays scheduled; no ad-hoc run is authorized.
