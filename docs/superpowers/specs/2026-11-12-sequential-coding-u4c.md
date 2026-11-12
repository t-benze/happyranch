# Sequential Coding U4c: selected Developer repository transport

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
server derives task/principal and the service derives the selected code-change
visit; browser/bearer, foreign, ended, recovery and thread sessions are refused.
Native browser audience is explicitly excluded; no TS mirror applies. Current
session checks bookend off-loop service execution; unchanged native selected
checks own each service admission. These are cooperative checks, not a new
atomic racing-session or filesystem fence.

Resolve is read-only and returns a fresh current-authority receipt on each
call. It never binds or releases editing. Bind forwards existing behavior:
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
