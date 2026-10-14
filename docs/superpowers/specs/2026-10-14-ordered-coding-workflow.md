# Ordered coding workflow — K0/K1 and K2a1 empty-only J source

> Status: K0 disabled catalog, K1 internal source, K2a1 empty-only J/migration source;
> populated K2a2 readers and K2b–K6 remain separately scoped work.
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
Errors contain category only. Temporary allocation is inside the owned error
boundary: failure is storage_unavailable with no launch, PID or acquired directory,
without publishing the raw OSError or attempted path. Once acquired, the parent's
exact directory identity survives every refusal, including a deadline before
resolution or during worker cleanup, known refusal, malformed/unknown reply and
final reap failure. Child omission and process quiescence never prove cleanup.
Worker recursive cleanup stays alarm-bounded; there is no unbounded parent cleanup
or retry. Success requires the parent's own no-follow absence witness; every
failure retains the identity as owner-only remediation metadata, without asserting
that storage remains present. Unknown launch/nested process/reap ownership stays
unknown after outer reap; a known nested PID is preserved on final reap failure.
The original five-second total deadline and reserve remain. Same-UID unmanaged mutation
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

## Empty-only coding schema — K2a1

Founder THR139seq562 approving559/329 and sole ROOT TASK10693 authorize this
bounded source slice. Layout J is exact I plus `workflow_coding_mappings`,
`workflow_coding_runs`, `workflow_coding_attempts`, `workflow_coding_events`:
3987 UTF8 SQL bytes/SHA256
`2c4b556671ef76582cdcbaed1e68d8575d9648ce29773f52c3d947ceaeeb0d70`,32 event
kinds,17 expected automatic constraint indexes (4/2/7/4), no explicit index or
marker. F–I DDL/history and document @1–@4/r1/r2/one-human formats remain literal.
The complete owned-layout reader and every metadata/cutover path admit J ONLY
with all four coding tables empty and valid retained I/document/native data.
Partial/extra/changed layout, any populated coding table, reserved coding
format/pin/import/source ownership or native coding audit without its readers
refuses; it never returns legacy. Arbitrary document content is not a family
discriminator; formats/kinds are checked in owned metadata. K2a2 must supply complete populated saved-data
readers before any coding producer, including setup/import/mappings.

Independent whole reference selection retains all six layouts F–J, all four
fresh/v0/v2/v2-organic histories, naming0/1, Pause and team-move combinations,
the five-component cache key and whole historical preimages. No candidate SQL
baseline or object filter is permitted. Failed reference construction remains
unavailable. Authority-v2 remains observed-only; generic Database/runtime-audit
remain workflow-free. The proven-empty J initializer primitive serves reference
construction, validating the two completed native remote-schema marker rows and
requiring every operational table empty; no schema object is excluded from
comparison. Actual create-org still explicitly initializes I. Startup/reopen/
enable validate, never install J; I remains usable for document work and gives
actionable offline guidance. Empty J uses the existing I draft/submission/handoff/
revision and document drain readers. Any J, even empty, blocks pre-J downgrade;
retain compatible J code or a separately approved verified pre-J backup. No
stripping, automatic migration or old-reader downgrade claim follows.

The explicit operator source is `scripts/migrate_workflow_coding_schema.py
--runtime-root /absolute/runtime --org ORG --check` (read-only:0 for valid empty
J,3 for exact I needing migration,1 for refusal). Only under separately authorized
operator control, use the same command without `--check` and with
`--confirm-cooperative-offline`. That flag affirms documented daemon stop,
reconciled hosts/outer tails and external exclusion of cooperative restarts/
publications/profile/move producers until return; it is not an OS isolation proof
or authority grant. Owned runtime/org markers, no-follow ordinary DB identity,
registered daemon lifecycle and removed PID/port files are rechecked. PID absence,
TTL, terminal task labels and producer_settled uncertainty never release an owner.
Complete source ownership checks include publication/profile leases and journals,
queued/active F5/draft work, recovery/source result processing, Pause entries and
release work, and team-move operations/reservations. Unknown closure refuses with
original-owner remediation. Legacy recovery effect claims without a persisted
host-closure reader remain unknown and refuse, even on terminal native tasks.
Exact I and retained SQL types/rowids/bytes/FKs are
validated before and after exclusive writer acquisition; any source change
refuses. Add only four empty tables atomically with FK enforcement ON, validate
independent complete J and exact retained equality, recheck offline identity,
then commit; every failure rolls back. Valid J replay is read-only and needs no
fresh offline migration. Earlier scripts report the actual already-compatible
layout and keep their original migration targets.

K0 stays disabled; K1 current resolution still refuses before private decode or
object I/O. No capability/setup/import/mapping/adoption/start/stage/native task/
event/audit producer, new route/DTO/native permission/dependency/hook, live SQL,
rollout/deploy/restart/assignment or trial renewal is delivered. Source inspection
and lint/static receipts cannot prove migration, readers, security, behavior or
upgrade safety: all remain UNEXECUTED. Python units RETIRED; general integration
INCLUDING callback SKIPPED24342; broad/negative/mutation/repetition/protected proof
UNEXECUTED/FOUNDER-WAIVED139429/430. Full independent review, same-final-head scoped
QA and Source Boundary Lint + Web Lint(Node24)/local all remain required, followed
by manager guarded merge. Root owns K2a2–K6 and operational/real-agent acceptance.

### Existing C15 case record — bounded K2a1 coverage

The accepted11414/11430 case design and manager step40 acceptance are reused.
The original C15 O/D/G/S, shipping seam, expected observable and future cold venue
are preserved below verbatim. These are design answers; no deliberate defect,
assertion, SQL, runtime or cold-compatibility experiment was executed.

| Case / input | Public observable / O | D | G | S | Future level | Environment | Isolation | Resource / prerequisite owner | Final transition / denial-no-effect oracle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C15 old I docs/@1..@4/one-human proposal, authentic old chain/fanout and new coding data; old binary/J + native heartbeat/PID/zombie/completion versus unknown load-bearing drift | Original bytes/chain advancement unchanged; document human choices work; old J reader refuses, no startup upgrade; legitimate native writer observations fold, unknown load-bearing drift refuses | Rewrite H/I or let coding task advance legacy active_chain; ignore SID/counter/cancel/PID drift or filter schema objects as timestamp workaround | New complete-layout/coding-version readers absent; coding whole native field/heartbeat and meaningful writer bridges absent | schema complete references/cutover scans/revisions old branch/recovery/run_step4979/chain.compute_advance_action; B2a native writer/audit→complete saved/reference/cold reader | Independent saved/reference/source inspection; later separately authorized cold compatibility | Genuine archived F–I document/proposal/old-chain data + complete J and mixed corrupt graphs; compatible archived readers; full raw native 27-column captures and independent fixed heartbeat audit records under matching J code | Whole layout×history×naming×Pause×move references; disposable DB/binaries, no SQL executed now | Schema/authority/reference/cutover source owners; engineering_manager admits future venue, independent QA checks integrity; native task/heartbeat/PID/zombie owners supply exact writer attribution | Whole authentic source accepted only by own family; orphan/mixed/cycle/fork/bootstrap-pointer corruption refuses without repair; even empty J pre-J refusal; document/human/old-chain behavior/bytes preserved; coding owner never legacy advances, startup never installs, historical integrity never current access; authenticated independent heartbeat folds without history rewrite and preserves saved validity, full native identity/ancestry/SID/counters/cancel/ownership equal; unknown/unattributed load-bearing drift refuses; whole-schema comparisons remain complete with no object filter; preceding reader refuses new coding authority/control/native-audit values |

| C15 K2a1 scenario | Expected observable at actual shipping seam | Source inspected / future isolated venue |
| --- | --- | --- |
| Exact empty J vs missing/extra/changed table/column/CHECK/FK/automatic-index definition | _validate_installed matches independent whole owned layout; mismatches refuse before legacy dispatch or writes | _canonical_layout/_layout/validate_empty_coding_data; future disposable owned org DB and independent literal comparison, never candidate-derived baseline |
| Existing I document @1–@4/r1/r2 and one-human histories on empty J | All old bytes, human commands, receipts/results/contributors and interpretation retain I dispatch; ordinary old chain/fanout remains native | Same _validate_draft_data/_validate_submission_data/_validate_author_handoff_data/validate_revision_storage, readiness helpers and cutover branches; future authentic archived F–I cases with J-compatible binary |
| One row in EACH of mappings/runs/attempts/events; orphan coding pins/import/context/provenance/submitted/join/scope/native result action/audit; unsupported coding ownership/format | Every installed reader including validate_data=False refuses categorically; no partial graph admission or legacy fallback | validate_empty_coding_data called after complete layout comparison for F–J, J retained validators forced; future isolated negative source-seeded DBs only after authorization |
| F–J × four histories × naming0/1 × Pause × team move, including cache identity and unavailable construction | Independent full five-component references preserve every old preimage; any missing reference refuses, all192 combinations have source branches | _release_schema_digest only J allowlist delta; _validate_release_database full SQL digest; future complete isolated whole-reference/cold matrix, not executed |
| Exact idle I→J, read-only complete-J replay, FKs, retained types/rowids/bytes | One exclusive transaction adds only four empty tables, no marker/index/row rewrite; validated J replay opens read-only and returns0; failed target/equality/FK check rolls back | migrate_coding_schema/_coding_org_identity/_coding_retained_rows and explicit operator script; future stopped disposable runtime/registered daemon home with authentic retained document/native data |
| Unknown publication/profile/F5/draft/recovery/source-claim/Pause/host/outer-tail/team-move owner; source DB/file/lifecycle/rows change before/after acquisition or target validation failure | Concrete original-owner refusal; no PID/TTL/terminal-label release, no DDL committed, retained rows unchanged after rollback | _validate_coding_source_idle + operator cooperative offline_check + BEGIN EXCLUSIVE/source equality/before-commit identity/except rollback; future isolated controlled owner/change/failure barriers |
| Startup/reopen/enable/current create-org and pre-J reader/downgrade | Startup validates only; create-org stays I; old reader refuses J, even empty; downgrade preflight gives coding_schema_requires_compatible_reader | OrgState guidance/unchanged install path, routes/orgs I call read-only, cutover _read/document scans/downgrade; future archived preceding-reader vs exact empty J and cold no-upgrade observation |

Each row is SOURCE INSPECTED only; behavioral/security/reader/migration/upgrade
safety and future venue results remain UNEXECUTED. No test source is added.
C01 applies here only to exact org identity, never actual mappings/override.
C09 applies only to migration read-only replay/source-change/rollback boundaries;
coding CAS/callback/heartbeat/control remains K2a2/K3. C12's literal lint checks
are this publication's observed engineering gates, never a new CI/merge producer.
C16 actual agent mappings/override/rejection/revision/gated merge and operational
acceptance remain same-root prerequisites with no new execution allocation.
