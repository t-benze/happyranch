# Ordered coding workflow — K0 catalog and K1 bounded repository source

> Status: K0 disabled catalog and K1 internal source; K2–K6 remain separately scoped work.
> Authority: Founder THR139seq562 approving seq559, confirmed563; sole ROOT TASK10693.
> Controlling amended design: manager step34 ordered-coding-contract.md,
> 195999 bytes, SHA256 `9f0cab1c4c2fb42610801ba77e38bab9d7c90c7314adbcb5e146228593e944a3`.
> Original292cf6d9 and DESIGN-ONLY QA11414 remain accepted historical evidence.

K0 publishes a fixed coding release definition so an existing org can inspect its
identity and bytes. It remains disabled regardless of current workflow state.
This source grants no coding execution, repository access, allocation or merge.
Historical document templates/contexts/receipts retain their original pins and
interpretation. Merged PR1030/1034 are inherited foundations; retained lower
drafts, failed TASK10867 and the retired trial remain history.

## Resource and immutable definition

The sole release source is `runtime/workflows/builtin_templates/coding.v1.json`:
634 bytes of canonical UTF8 compact sorted JSON, without newline, SHA256
`6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`.

| Key | Exact value |
| --- | --- |
| format | workflow-coding-definition@1 |
| identity / version | release/happyranch/coding / integer1 |
| artifact_kind | code-change |
| roles | implementer, code_reviewer, qa_engineer, agent_manager; all agent |
| stages | development, code_review, qa, manager_closeout; in that order |
| revision_policy | max_revisions3; invalidate all-dependent-approvals; return_to development |
| replacement_policy | same_principal_retries1; uncertain hold; selector bound-agent-manager |
| closeout | required true; checks Source Boundary Lint, Web Lint (Node 24); ci_conclusion success; merge guarded-expected-head |

The bounded strict validator rejects duplicate/unknown/missing keys, wrong native
types (including Boolean/integer substitution), wrong values/order/bounds,
nonfinite/BOM/noncanonical/changed bytes and digest mismatch. Exact normative
hash and634-byte length remain required after parsing. Pins are exactly
workflow-coding-compiler@1, workflow-coding-validator@1 and release-input@1.
Numeric publication version1 is separate from these families.

The reader resolves only the constant `importlib.resources.files("runtime")`
package path `workflows/builtin_templates/coding.v1.json`, reading at most16384 bytes. Exact634-byte validation rejects longer
resources, including a bounded prefix that reaches that limit. It accepts no caller path, fallback,
configuration, DB/default store or mutable readiness. Import and router mounting
perform no resource read. Unsupported/missing resources hold the catalog; corrupt
resources never silently replace the release or enable it.

Exactly one `pyproject.toml` wheel.force-include source/destination pair supplies
this file. `packaging/daemon.spec` consumes that same inventory unchanged for both
Analysis inputs, mapping a file destination to its containing directory. Source,
wheel and supported frozen loaders select the same constant resource path. Actual
installed/frozen loading and build inclusion remain UNVERIFIED; static inventory
cannot prove them. No package/native build is authorized by this publication.

## Authenticated GET and actual CLI caller

GET `/api/v1/orgs/{slug}/workflows/release-templates/coding/1` uses the existing
bearer dependency and existing-org lookup before projection/package reading.
Only this literal kind/version is mounted; other kind/version paths are404.
The closed response is:

```json
{
  "format": "workflow-release-template@1",
  "identity": "release/happyranch/coding",
  "version": 1,
  "definition_bytes_base64": "<canonical base64 of the exact634 bytes>",
  "definition_digest": "6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8",
  "compiler_pin": "workflow-coding-compiler@1",
  "validator_pin": "workflow-coding-validator@1",
  "source_pin": "release-input@1",
  "readiness": {"enabled": false, "reason": "release_catalog_only"}
}
```

Readiness is immutable disabled catalog data; there is no readiness setter,
DB/authority lookup or route import effect. Missing/unreadable/unsupported
resource gives503 `{"detail":{"code":"resource_unavailable"}}`; corrupt bytes
give500 `{"detail":{"code":"resource_corrupt"}}`, without paths or raw causes.
Existing authentication401, org404/runtime409 and daemon-auth configuration500
retain their existing meanings. GET performs no workflow writes or allocation.

`happyranch workflows release-template coding --version 1 --org ORG --json` makes
this actual GET through the existing bearer transport, without session admission
or stage supplement. Org selection uses existing CLI rules. The shared closed
DTO validates pins, strict types, canonical base64 and exact decoded bytes before
stdout. Valid catalog exit0 proves no execution readiness; parser refusal exits2,
transport/server/malformed receipt exits1. There is no retry or local replacement.

OpenAPI exposes the closed response model. The maintained summary pins the
literal GET and parameters/statuses. Route classification includes this ordinary
org GET, preserving current native default handling with zero policy edits; it
adds no native reachability grant. K0's explicit approved radius has no TS,
provider, browser initiation or stage-prompt callers. Browser UI work belongs to
later K6; no included classification is an execution permission.

## Later serial boundaries and amended v1 merge

K1 internal adopted-store mechanics and unavailable current-use service are described below. K2 independent J readers/offline source,
reusable Founder setup/withdrawal/attenuation, explicit release import/mapping
and default/override; K3 development/review with genuine result/task/control and
native ownership; K4 QA/revision/replacement; K5 CI/guarded merge/closeout; K6
operator UI/cutover remain separately scoped publications. K0 implements none of
those writers, schema, capability issuers, task allocators or effects.

Founder562 supersedes historical mandatory target-base merge/cutover conditions.
V1 requires the exact independently reviewed head plus successful required
lint/static CI on that head, independent scoped QA, normal hooks and guarded
manager merge. Conditional target-base support is optional later and cannot block
v1 cutover. The artifact comparison-base identity stays fixed across review and
QA; a moving target tip is provenance, not an atomic v1 gate. Changed D1/D2 and
C13/C16 interactions need independent DESIGN-ONLY assessment in K0's scoped QA
and same-root acceptance before dependent K5 work. K0 consumes no effect adapter
and claims no execution evidence for those changed cases.

No live migration, capability issuance, deployment, restart, assignment, provider
effect, trial revival or cutover is authorized. DB/store/authority imports,
J producers, setup/mapping/import writers and execution-readiness claims are
absent. Future releases retain old files/readers; no history rewrite follows.

## Evidence and finite accepted cases

Reuse accepted K0/F4 cases: canonical bytes/hash; duplicate/unknown/type/bounds
refusal; missing503/corrupt500 categorical resource errors; existing org auth
before projection; unknown kind/version404; actual CLI/closed-schema parity;
immutable disabled readiness/no workflow writes; one shared package inventory
entry and constant source/wheel/frozen resource path. Source inspection and
separately authorized inert OpenAPI generation/check are the permitted evidence.
No new behavioral assertions or retired Python unit sources are added.

Independent full code review and honest same-final-head scoped QA remain required.
Local supported all/hosted Source Boundary Lint and Web Lint (Node24) remain
lint/static only; normal hooks/nonforce publication/manager guarded merge remain.
Python units RETIRED, general integration INCLUDING callback SKIPPED24342;
broad/negative/mutation/repetition/protected proof UNEXECUTED / FOUNDER-WAIVED
139429/430. Source/schema receipts are not behavioral, installed/frozen-loader,
engineering-chain, live-upgrade or whole-program acceptance.

## K1 internal repository artifacts

The service mount in OrgState is inert. `resolve(session_id, selected_id)` always
raises workflow_repository_reader_unavailable before inspecting selectors,
decoding private descriptor/capsule bytes, probing filesystem objects or awaiting
locks. K2b's genuine adoption/authority producer is absent. A saved proof model,
digest, Boolean, constructor flag or callback cannot enable the mount. There is
no public raw-reader bypass, route or new DTO; code activation's existing refusal
and K0's disabled readiness remain unchanged.

Saved readers close `workflow-repository-binding@1`,
`workflow-repository-adoption@1`, `workflow-repository-adoption-proof@1`,
`workflow-repository-presentation@1` and the B resolution projection. They reject
extra/missing/duplicate/noncanonical keys, wrong native types, bad identity/hash,
base64, object format, modes, UTF8, paths/order and bounds without filesystem I/O.
These records establish historical integrity, not original traversal, provenance,
current access or execution rights. The private raw reader independently derives
only OrgPaths.workspaces_dir/store_owner/repos/repo_key from server records;
configured origin is compared exactly without contacting it, and recorded
repo/.git/objects device/inode plus config digest are checked and reopened.

Only an ordinary primary .git directory is supported. No-follow component/file
opens reject symlinks, hardlinked files, worktree/common-directory indirection,
alternates, shallow/promisor stores, replacement/graft and ambiguous/helper
configuration. The closed ordinary-clone config subset refuses unknown extensions
or helper paths. Private object-only storage receives bounded regular loose
objects and paired v2 pack/index files; source refs, hooks, attributes, worktree
and config are never inherited by Git. Explicit sanitized cat-file argv/environment,
bounded type/size/body reads, raw object hashing and raw tree traversal select
exact commit endpoints, with no branch/PR navigation, diff/rename heuristics,
checkout/fetch/network/helpers/filters/textconv/drivers/submodule/LFS execution.
Unchanged unsupported content is refused too; no path is skipped or truncated.

Bounds are descriptor/full scope1MiB, commit64KiB, tree256KiB, blob64KiB,
4096 objects,64 levels,512 presented changed paths,8MiB aggregate read/inflation,
64MiB copy,256KiB canonical presentation and5-second total deadline with bounded
process memory/output and kill/reap. Snapshot manifests contain every supported
head blob; comparison manifests contain every changed path in the exact base/head
union with before/after mode/OID/UTF8 bytes, sorted by raw UTF8 path. Missing or
wrong-type objects refuse; verified hash/pack corruption is storage corruption.
Errors contain category only; unreaped ownership/private storage remain internal
unknown facts rather than success or quiescence. Same-UID unmanaged mutation
remains a storage hazard, not OS isolation or remote-origin provenance.

K2b supplies authenticated setup/withdrawal/attenuation/mapping/adoption producers.
K3 must deliver result-owned coding start, development staging/fulfillment,
selected attempt GET, prelaunch and receipt consumers; K4 adds QA/revision/current
join consumers; K5 adds exact-head CI/guarded merge/closeout consumers. Each current
use authenticates selected org/principal/session/capsule/adoption/current authority
before any private decode/probe, resolves outside writers, then reselects and
revalidates after awaited work before disclosure/effect. No historical receipt
shortcuts current access. Existing document writers/readers receive no non-null
repository slot or changed interpretation in K1. No J write, capability issuance,
live adoption/setup, execution/readiness, migration/deploy/restart/assignment or
retired trial renewal is authorized.

Accepted finite cases C03/C04/C14 and B03/B04 are reused in the maker evidence.
Source/static assessment only is authorized. Object-reader execution, security,
denial/race/bounds/cleanup assertions, provider experiments and live fixtures
remain UNEXECUTED; no new test source or campaign. Python units RETIRED;
integration INCLUDING callback SKIPPED24342; behavioral/negative/mutation/
repetition/protected/executable-QA proof UNEXECUTED/FOUNDER-WAIVED139429/430.
Independent FULL code review then honest same-final-head scoped QA remain
required; no maker review, QA or merge. Static-only all/hosted checks and normal
hooks/nonforce publication are required, with guarded manager merge later.
