# Ordered coding workflow — K0/K1 and K2a1 empty-only J source

> Status: K0 disabled catalog, K1 internal source, K2a1 empty-only J source;
> cooperative offline migration source restored by step43; operational proof unexecuted;
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

The operator source `scripts/migrate_workflow_coding_schema.py --runtime-root
/absolute/runtime --org ORG [--check] [--confirm-cooperative-offline]` implements
the approved cooperative offline I→J path. All invocations open read-only first:
valid I --check returns3 (migration needed), compatible empty J replay returns0,
and invalid or unavailable observations return1. Only exact I with explicit
cooperative offline confirmation can reopen mode=rw. The independently resolved
owned org path and pre-open device/inode/mode observation bind the RO PRAGMA
main path; ORIGINAL identity survives RO close/RW reopen, is compared before
and after writer open, after BEGIN EXCLUSIVE and before commit. Main/path and
attachment checks, regular/single-link/current uid, org root/slug/marker and
symlink constraints retain the shipped G pattern. Missing observations or known
path/file/lifecycle drift refuse with original-owner remediation; the writer's
replacement file never becomes a new source baseline.

This is cooperative pathname/file observation, not independent native descriptor
proof or hostile/direct same-user swap protection. Founder270 approving265/269
and Founder562 preserve that limit. Step43 withdraws step41/42's excess native
opened-file bridge requirement; their categorical binding_unavailable refusal,
FAILED11447 and adverse11446/11448 receipts remain historical. No native bridge,
extension/dependency/VFS/raw-pointer/ctypes change is supplied or needed under
this model. Direct same-user file/DB swapping between observations remains a
disclosed cooperative limit; source-only delivery cannot certify its behavior.

Writes additionally require documented daemon stop, reconciled hosts/outer tails
and exclusion of cooperative restarts/publication/profile/move until return.
Neither confirmation nor PID absence, TTL, terminal labels or producer_settled
uncertainty releases ownership. Complete source-idle checks retain publication/
profile, F5/draft/recovery/source claims, Pause release and team-move reservations.
Exact I/source SQL types/rowids/bytes/FKs, unchanged retained equality, four empty
additions, complete independent J validation, FK ON and rollback remain required.
The primitive receives the script's original source identity and independently
compares it, never derives a replacement baseline. Runnable source is supplied;
valid check/replay/migration/rollback and drift refusal remain UNEXECUTED, with
no migration-safety or OS isolation claim. Earlier scripts keep original targets.

The finite shared discriminator inventory covers typed scalar fields and
structured metadata using the same rules. Malformed lexical member scanning
recognizes complete, escaped, duplicate and incomplete reserved envelopes
without searching quoted document prose. Authority `source_pin` and coding
setup namespaces are checked independently of canonical/hash-valid bytes,
including orphan rows and current authority pointers. Typed scalar/native
`business_purpose=coding-replacement-selection@1` participates in the same
valid/malformed/duplicate/escaped/incomplete metadata rules. Ordinary
worker_execution/session purpose and free document prose remain noncoding.
Recovery.classify_task runs the empty/reserved boundary under its existing lock/error fence before
no-workflow and no-root/draft/F5 legacy returns; any coding-family schema
object, including unknown/case-changed names or wrong types, also requires the
complete central reader. Names and owning-table names are each checked for
tables/views/indexes/triggers; no old ownership alias is needed. Missing org identity
reconciles categorically before either legacy return. Unknown/malformed coding
returns reconciliation_required, with no coding ownership, task writer or dispatch.
Ordinary noncoding legacy and the existing document draft/F5 paths remain.

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
| Exact idle I→J, read-only I check and complete-J replay, FKs, retained types/rowids/bytes | Expected check3/replay0; explicitly confirmed cooperative migration adds four empty tables with FK ON/full target/retained equality and rollback on failure. Source branches are runnable but outcomes unexecuted | migrate_coding_schema/_coding_org_identity/_coding_retained_rows and operator script; future separately admitted stopped disposable owned runtime with authentic retained data and original RO→RW identity |
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


### C15 R1–R4 correction — finite discriminator and negative-consumer inventory

Manager step41 accepts independent11446 R1–R4 under Founder139562/559/329;
step43 accepts11448 typed-purpose/schema-object findings and restores the
Founder270/562 cooperative migration model, withdrawing excess native descriptor
proof. Unchanged accepted11414/11430 C15 and applicable C01/C09/C12/C16 are reused.
This inventory describes source assessment; none of these cases executed.
Shared `_coding_scalar` drives scalar scans, successful object-pairs decoding
and malformed-envelope lexical member decoding. There is no second regex list.
All retained structured `_bytes`/`_json`/`payload` columns in workflow tables and
native tasks/task_results/audit_log are inspected; submission_bytes and
candidate_bytes remain arbitrary content, not typed metadata.

| Typed discriminator | Complete finite value rule | Actual scalar location / structured location |
| --- | --- | --- |
| format | workflow-coding-*, workflow-coding@*, workflow-code-change@*; coding-scope@1 and coding-replacement-selection@1 | Typed format in every saved scope/definition/context/provenance/request/proof/join/event/authority/control/native metadata; covers all accepted @1 families and refuses unsupported later family versions |
| compiler_pin / validator_pin | workflow-coding-* | workflow_template_drafts and workflow_template_versions; the same typed fields in metadata |
| source_pin | coding-capability-issuer@1 or workflow-coding-* | workflow_authorization_revisions and template draft/version source_pin; typed metadata, including orphan authority with canonical/hash-valid bytes. Shared release-input@1 alone is not coding |
| namespace / authority_namespace / org_namespace | release/happyranch or org/{nonempty-slug}/coding-setup subtree, including incomplete key suffix | Template identities/drafts/versions, authorization revisions/active pointers, authority pointers/publication journals/leases/admission records, profile dependencies, activation/dispatch/draft authority_namespace; matching typed metadata, irrespective of attached instance |
| kind / artifact_kind | code-change | Typed metadata, including native decisions; arbitrary prose or role/principal names are not discriminators |
| source_kind | coding-manager-result | workflow_contexts.source_kind and typed metadata |
| business_purpose | coding-replacement-selection@1 | Accepted contract lines196/198/253/258: native audit_log session_start/control payload and saved control scope; any matching scalar workflow/native metadata field. Ordinary invocation_purpose/session_purpose=worker_execution alone remains noncoding |
| action | workflow_start; coding_* (retains prior scalar audit LIKE case tolerance) | task_results.decision_json/action metadata and audit_log.action/payload; no parsing of task brief/summary/findings |
| identity / release_identity | release/happyranch/coding | Typed release metadata and coding mapping scalar (any mapping row already refuses) |
| coding schema objects / rows | Reserved workflow_coding_ family, case-insensitive, plus exact workflow_coding; name OR owning table; any object kind; any row in each literal J table | Recovery checks tables/views/indexes/triggers including unknown names, wrong types and case changes before both legacy returns; full central validation, or categorical reconciliation absent org identity. No schema filtering/install |

For each typed family, the finite negative selection includes complete scalar
and structured identity, nested array/object identity, escaped key/value,
duplicate coding→document and document→coding values, missing outer closing
brace/comma-tail and incomplete quoted value, including UTF8/16/32 byte
envelopes supported by the successful JSON decoder. The lexer consumes quoted prose
as one token; it does not search inside descriptions or interpret raw document
content as member metadata. Invalid/truncated escapes retain only their decoded
prefix; a reserved or still-possibly-reserved incomplete typed value refuses.
Oversized/deep metadata refuses, never truncates a row prefix into acceptance.

| Correction / scenario and action | O: observable expected result | D: deliberate defect to expose later | G: exact missing guard/dependency | S: actual shipping seam and final denial oracle |
| --- | --- | --- | --- | --- |
| R1 every inventory entry in incomplete/duplicate/escaped native decision, including action=workflow_start, kind/artifact_kind=code-change, source_kind=coding-manager-result, business_purpose=coding-replacement-selection@1; compare ordinary worker_execution purpose and prose containing the same words/quoted snippets | Full/metadata readers reject unsupported coding; recovery yields reconciliation_required. Ordinary document strings stay outside typed discrimination | Remove an inventory entry or restore partial fallback regex/last-key-wins | Shared scalar and lexical member discriminator before malformed-native absence | _refuse_coding_metadata→validate_empty_coding_data→_validate_installed/classify_task; no dispatch/claim/schema/task effect, no fabricated legacy validity PASS |
| R2 canonical/hash-valid orphan authority with reserved source_pin, setup namespace alone or both; current pointer with no linked document instance | Unsupported coding authority refuses regardless of canonical/hash integrity or old closure absence | Inspect authority_bytes only or omit scalar authority/pointer namespace | Complete typed scalar inventory independent of stored canonical-byte decoder | All workflow scalar columns→_coding_scalar; native malformed BLOB scalar cannot hide a reserved family; neither setup reader nor issuer/write exists |
| R3 no workflow tables but reserved native result/audit; valid old F–I with orphan coding and no draft/F5 alias; populated/partial/empty J with no old ownership; unknown coding table/view/index/trigger, wrong object type or case variation, including generic no-workflow graph; schema/scan error or unknown org | Every relevant early path reconciles/refuses before legacy; generic noncoding remains legacy, valid retained document draft/F5 still follows original owners | Move the guard after no-tables/no-roots legacy, or validate only old aliases | Pre-return empty/reserved scan and complete coding schema validation under existing lock/error fence | classify_task→runner.enqueue_task/jobs.submit/run_step prelaunch/completion/startup recover_owned_task; no queue/dispatch/coding task owner; all consumers read-only |
| R4 original owned path/pre-open baseline, valid I check, empty-J replay, explicitly confirmed stopped/idle I migration, target failure/rollback; known path/dev/inode/mode/lifecycle drift before/after RW reopen, after-exclusive or precommit; unavailable observation | Expected RO check3/replay0 without writer; valid migration retains exact source rows and four empty additions; invalid source/confirmation/owner/observation or known drift refuses, no DDL committed and rollback preserves source. All operational results UNEXECUTED | Omit expected path/mode, reset baseline from RW writer, skip exclusive/precommit identity/offline/whole-source checks, or claim direct same-user swap exclusion | Cooperative exact-main/path/attachments and regular single-link uid/dev/inode/mode observations using ORIGINAL RO baseline; source-idle/whole-release/typed-row/FK validation; no native FD proof requirement | Operator independently resolved path/pre-open observation→RO _coding_org_identity→pre/post RW comparison→migrate_coding_schema after-exclusive/precommit→exception rollback; future authorized stopped disposable runtime, no SQL/security experiment now |

Future level: independent scoped reader/recovery/cold migration checks in an
explicitly admitted disposable venue; runtime/security/SQL execution remains
UNEXECUTED. Future isolation: distinct synthetic orgs and private DB/source paths,
separate stopped runtime plus registered daemon home and genuinely reconciled
host/outer-tail owners; no shared live daemon/org/store/port or production swaps.
Resource owner: ROOT10693 separately authorizes any venue/execution; original source/host/publication owners reconcile
uncertainty; independent QA owns observations, maker never supplies a verdict.
C01 preserves actual-org binding without claiming mappings/override; C09 adds
unavailable-observation/original RO→RW/check/replay/migration/rollback/no-alias recovery boundaries without coding CAS or
callback implementation; C12 remains exact-head lint/static only, with genuine
pending/failure evidence; C16 real-agent/revision/mapping/override/operational
acceptance stays root-owned. Python units RETIRED; integration INCLUDING callback
SKIPPED24342; behavior/security/migration-safety/negative/mutation/repetition/
protected/executable proof UNEXECUTED/FOUNDER-WAIVED139429/430. No test-authoring
or behavioral PASS follows from this four-answer source case amendment.


## Saved coding grammar source — K2a2a

Sole ROOT TASK10693, manager step48 and Founder THR139seq562/559/329
bound `runtime/workflows/coding_saved_records.py` to pure, unmounted saved-record
inspection. All eight entry points check complete canonical bytes, strict types,
bounds and contained equalities. Original command bytes/IDs/CAS/selectors remain
intact; contextual coding repository roles preserve K1 identity/owner invariants
without widening document/K1 readers. Parsing creates no adoption or authority.

Accepted TASK11459 B2/D1 appendix SHA256
`5a1baa4967ffefb07967d1aa5e5771b6048801bfcd71b37d690ab74aa3722c9b`
closes ready/pointer/profiles/full mirror, exact Founder provenance and complete
five-column native admission plus capture on issue AND withdrawal. Native v1/v2
snapshots retain their complete original projection and ordering; reconstruction
includes all consumers, generations and digests. Genuine empty profiles differ
from missing evidence; other-namespace/removed/unbound/unused mirror rows remain
validated. Historical pointer/journal/fence are retained, never read from today.

Grant selection is position-specific: setup/issuance terminates in full original
issue/withdraw Q; mapping uses closed M with 1–8 ordered distinct complete U
setups; capability selects its permitted U/P parent. Complete preimages/hash,
ID/parent/time/source ownership, source versus mint admission, level/action,
queued-null versus actual bound SID and component/argv radius attenuation are
checked without format fallback or truncation. Each ancestry ends at U→issue Q,
with at most64 capability nodes/16MiB decoded ancestry and original1MiB limits.
Mapping cannot union one repository's setup onto another. U subtree digest is
not the containing native issuance-wrapper digest. Fixed repository/base/head
and provider subject are separate from navigation. Saved ready/Founder strings,
a hash or a parsed grant never prove authentic native rows or current access.

Full relational/native↔J/event/history readers remain required BEFORE every
producer: identical genuine admissions and historical cache_installed journals,
setup issuance-subtree/native-wrapper/pointer/withdrawal, source result/start
claim and native task/session/parent chronology, mapped role eligibility and
independence, mapping generation/adoption authenticity, run/context/binding/
activation/request/receipt/contributor closure, journal replay/CAS and host/
cleanup/provider/effect ownership. Current-use owners revalidate every setup/
ancestor/grant, cancellation/Pause/move and actual executor permissions after
waits. Valid revoked history permits no new launch/disclosure/effect. No store,
roster, object, provider, DB or network I/O, resolver or trusted flag is supplied.

K2a1 still refuses ALL populated/reserved coding data including validate_data=False;
F–I/J schema/reference/cache/recovery/migration owners, create-org I and document
history are unchanged. K0 stays disabled and K1 current resolution unavailable.
No mount, native admission insertion, producer, transport/auth/permission change,
live setup/migration/assignment/deployment/restart or retired trial follows.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/negative/mutation/repetition/protected/executable proof UNEXECUTED/
FOUNDER-WAIVED139429/430. AST/raw-byte inspection and lint/static checks establish
no behavioral or test-authoring PASS. Fresh FULL independent review then
same-final-head scoped SOURCE QA and manager guarded merge remain required.


## Retained native setup issuance history — K2a2b1

Sole ROOT TASK10693 step50 / Founder THR139seq562/559/329 adds unmounted
`coding_authority_history.read_setup_issuance_history`. The caller must admit
the complete native schema and own a coherent read transaction/connection/lock
with raw native storage values. Fixed-table parameterized SELECTs only; no
BEGIN, commit, writer, repair, resolver or injected truth flag is supplied.
The org-wide setup namespace inventory checks complete canonical issuance
wrappers, exact native types, source pin `coding-capability-issuer@1`, namespace,
ID/revision/time, full original command, expected-revision CAS and unique
Founder operation keys/setup subtrees. The selected native authorization ID
resolves one contiguous same-namespace prefix with a genuine prior issue for
withdrawal. Setup IDs and authorization row IDs are distinct identities.

For every issue AND withdrawal in that prefix, the complete five-column native
admission matches the saved command digest/Founder principal/org publication
namespace/generation/ID. The named complete twelve-column historical journal
must match namespace/generation/expected generation, exact full snapshot
bytes/digest/profile fence and cache_installed state, without native NULL/type
coercion. The shipped pure readers reconstruct saved v1/v2 snapshot/profile/full
mirror evidence, including genuine empty profiles. Neither today's pointer,
cache, roster nor mirror is historical proof. Exact setup_bytes are the issue's
canonical subtree; its digest differs from the native issuance-wrapper digest.
Valid old issuance remains history after withdrawal and never restores activity.

All three SELECTs gate every projected value by exact native storage class
before Python fetch: TEXT plus byte length via CAST AS BLOB (including multibyte
characters and embedded-NUL suffixes), BLOB plus byte length, INTEGER scalar
only. Invalid/oversize values return a bounded NULL refusal sentinel while
original typeof evidence stays intact. Genuine nullable file_phase_owner NULL
is preserved; accepted values retain exact historical bytes/types without
coercion or truncation. Each projected text/BLOB value is at most1MiB.
Inventory admits at most1024 org setup rows; one additional bounded row can be
fetched only to refuse overflow. After each bounded fetch, the shared budget
enforces16MiB cumulative accepted raw native text/BLOB bytes across inventory
and required admission/journal reads. This is retained-byte accounting, not a
total process-memory ceiling: SQLite engine work, rejected fetched rows and
decoded/copied structures are outside that accounting. Overflow/missing/
mismatched/ambiguous/malformed/unsupported evidence refuses categorically.
Only the selected prefix's admissions/journals are established, not unrelated/
later histories. No live human, current grant, source task/SID/result or populated-J
admission follows. Full capability/task/result/session ancestry, mapping/J/run/
event/receipt/heartbeat and current-use closure remain NEXT serial units before
any producer; K2a2 is incomplete. No mount/OrgState/DDL/reference/cache/recovery/
migration/selector or public/CLI/TS/auth/permission/dependency/hook change.
K2a1 populated/reserved refusal INCLUDING validate_data=False, create-org I,
document history, disabled K0 and unavailable K1 current resolution stay intact.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/migration/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Source/static evidence is not behavioral acceptance.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no operational action or trial renewal is authorized.


## Retained mapping history and setup links — K2a2b2

Sole ROOT TASK10693 step53 / Founder THR139seq562/559/329 adds unmounted
`coding_mapping_history.read_mapping_history`. Caller admission of the complete
native/J structural schema and one coherent raw-value read snapshot/connection/
lock precede this SELECT-only call. It neither starts nor ends a transaction.
The selected native mapping ID resolves its complete same-org/release generation
prefix, never the current default. All ten native columns and original typeof
labels are retained, with full saved mapping bytes/digest, original complete
command/digest/key, strict INTEGER generation and expected_generation CAS,
original time and operation principal independently compared. Attribution follows
`WorkflowTemplatePrincipal.operation_principal`: agent:<issuer.principal>,
separate from raw role IDs and human:founder issuance provenance. Missing/gapped/
forked/conflicting operations refuse; no newest-row or same-digest fallback.

For each required mapping, its actual complete five-column admission matches the
original command digest/agent operation principal/capture namespace/generation/
ID. Its historical twelve-column publication journal matches full snapshot bytes/
digest/profile fence/expected generation/cache_installed and original nullable
owner. Shipped `read_saved_mapping_record` checks complete canonical mapping/
command/adoption/M grammar, setup prior-source and adoption times, repository
radius and all binding/adoption/capture pin equalities. Each M.setup resolves by
setup ID AND full canonical bytes to one genuine issuance.setup subtree, then
its complete earlier issuance/admission/publication prefix. The private native
setup composition shares the existing history owner; wrapper hash is distinct
from setup hash. Founder issue/withdraw semantics and authentic later withdrawal
remain historical facts; this read never restores a grant. No current pointer,
roster, cache, filesystem/store scan, adoption or remote-origin attestation.

Every projected value in all four fixed-table parameterized SELECTs gates exact
SQLite storage class BEFORE fetch: TEXT byte length via CAST AS BLOB, BLOB byte
length, native INTEGER scalar; genuine owner NULL remains NULL. Invalid/oversize
values become bounded NULL refusal sentinels with original typeof evidence, not
coercion/truncation. Per text/BLOB value at most1MiB; each mapping/setup inventory
at most1024 rows plus one bounded overflow fetch solely for refusal. Across the
whole composed call one shared budget limits8192 accepted native rows,16384
traversal steps and16MiB cumulative accepted raw text/BLOB bytes after bounded
fetch. Call-local indexed inventory/verified history can avoid repeated reads;
no per-root/recursion budget reset. SQL engine work, rejected-row and decoded/
copied structures are outside this accounting; no total memory ceiling claim.
Existing `read_setup_issuance_history` signature/result and retained semantics
remain unchanged; its established smaller inventories fit these added call caps.

Return values establish complete inventory grammar/attribution/operation
uniqueness and selected mapping-prefix admissions/publications plus the required
setup issuance prefixes only. They do not authenticate actual issuer task/SID/
policy/source_rule/current grant/capability/J graph or live store observations.
Full capability/native task/result/session/ancestry/J/run/event/request/receipt/
contributor/heartbeat/current-use/recovery/cutover readers still precede EVERY
producer; K2a2 remains incomplete. No mount/__init__/OrgState/schema/reference/
cache/recovery/migration/selector/producer/public/CLI/TS/native/auth/permission/
dependency/hook change. Populated/reserved coding refusal INCLUDING
validate_data=False, create-org I, document history, disabled K0 and unavailable
K1 current resolution remain intact. Root retains K2a2–K6 and genuine-agent/
two-org/override/backup-copy operational delivery. Accepted C01/C03/C09/C14/C15/
C16 and appendix variants remain source-scoped; no new case study/execution.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; parser/SQL/
product/security/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Static lint/source assessment is not behavioral proof.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no maker verdict/merge/operational action or trial renewal.


## Retained native capability ancestry — K2a2b3

Sole ROOT TASK10693 step56 / Founder THR139seq562/559/329 accepts capability
encoding contract SHA25638d80c7a4cc7a3283fdea37dd6f21fb366df9c2eba5fc47415ebe2061051fc7f
following independent DESIGN PASS11478. Unmounted
`coding_capability_history.read_capability_history` adds the retained capability
closure after the earlier K2a2b1/b2 checkpoints. Their historical descriptions
of then-missing capability readers remain history; full K2a2 is still incomplete.

A selected actual authorization ID resolves all seven native columns: ID=P.id,
namespace=org/{org}/coding-capability/{P.id}, INTEGER revision1, complete canonical
P BLOB, full-P SHA256 TEXT, literal workflow-coding-capability@1 source pin and
exact P.created_at TEXT. ID/namespace/digest aliases conflict rather than act as
fallbacks. Each P's OWN complete five-column native admission matches its capture
ID/org publication namespace/INTEGER generation, H(complete P) request_digest
and historical agent:<P.issuer.principal> operation attribution. Neither parent
capture nor original source_admission_id substitutes for a child's admission.
H(P) is finite; capture.grant_digest still hashes the complete upstream P or U.
Historical attribution does not authenticate a currently acting source/service.

The existing pure saved reader validates the selected complete canonical P/U/Q
ancestry once, including immutable source ownership/admission, level/action,
component/argv radius attenuation, refinements, queued-null versus bound SID
and effect subject equalities. Every embedded P must then resolve to its OWN
native row with byte-identical complete P, exact envelope, own admission and
historical publication capture. Terminal U resolves by setup ID AND complete
canonical subtree bytes to the actual containing issue plus complete preceding
issuance/admission/publication history. U ID is not an authorization row alias;
H(U) is not H(issuance wrapper). Terminal original ISSUE Q stays intact. Missing,
duplicate/conflicting/cyclic/cross-org/wrong-type/mismatched evidence refuses
with one category, no private bytes or SQL diagnostics and no partial history.

Caller must admit the complete native/J structural schema and own one coherent
raw-value read transaction/connection/lock. Fixed parameterized SELECTs only,
no transaction control. All new/reused projected values gate exact SQLite storage
class and byte length BEFORE fetch; TEXT uses BLOB byte length including NUL
suffixes/multibyte characters. Original typeof/refusal sentinels and genuine
nullable journal-owner NULL survive; no coercion or truncation. Each variable
value is at most1MiB. One shared whole-call budget permits8192 accepted native
rows,16384 traversal steps,16MiB accepted raw TEXT/BLOB bytes, setup inventory1024
and selected capability ancestry64. One bounded overflow fetch only refuses.
Call-local setup caching retains complete histories; no recursive budget reset.
Enclosing/decoded saved bounds remain and can refuse before64 nodes. These raw
byte limits do not bound total SQLite/process/decoded-copy memory.

`CodingCapabilityHistory` retains source-to-selected native capability facts,
all seven columns/original typeof/matched own admissions, and actual setup issue
history. Later withdrawal preserves history without restoring any grant. No
current pointer/cache/roster/store/object/network is read. Necessary timestamp
equalities/order do not prove actual mint order, source execution, native
allocation/task/SID/result/assignment/ancestry/policy, acting-service ownership
or full J graph. Complete native source/action-claim/task/session/result/policy,
run/context/binding/activation/event/replay/request/receipt/contributor/heartbeat,
current-use/recovery/cutover readers still precede EVERY producer. No source-rule
verifier, current permission, genuine launch or provider effect follows.

Existing setup/mapping APIs and source owners are unchanged; their bounded
private composition suffices without a new helper or owner edit. No mount/
__init__/OrgState/producer/schema/reference/cache/recovery/migration/public/CLI/
TS/native/auth/permission/dependency/hook change. Populated/reserved refusal
INCLUDING validate_data=False, create-org I, document history, disabled K0 and
unavailable K1 current resolution remain intact. Original contract9f0cab1c,
appendix5a1baa49/cases17d50aa2 stay byte-preserved; only the four accepted native
envelope/own-mint/full-ancestry/history-and-bounds variants are appended to task
case evidence. Python units RETIRED; integration INCLUDING callback SKIPPED24342;
all SQL/parser/product/security/negative/mutation/repetition/protected executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Static/source assessment establishes
no behavioral or test-authoring PASS. Fresh FULL independent reviewer then
same-final-head scoped SOURCE QA return to SAME root, with actual lint/static
checks, normal publication/hooks and manager guarded merge. Root retains full
K2a2–K6 and real-agent/two-org/override/backup-copy operational acceptance; human
trial remains retired and no live operation is authorized.


### Retained native source identities — K2a2b4

ROOT10693 step60 / Founder139562/559/329 accepts TASK11484 appendix
4ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1/cases
f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae after DESIGN11485.
Unmounted `coding_source_history.read_source_native_records` composes genuine
source-level P and complete native P/U/own-admission/publication history, sharing
one call budget/setup cache with the existing unchanged public readers. It returns
exactly immutable capability_history, source_task_record, ancestor_task_records,
session_start_audit_record and same_session_result_records.

Complete27 task/18 result/6 audit columns retain native storage classes/NULLs,
empty free text, all UTF8/JSON bytes, original timestamp spelling and finite REAL
cost. Source/parent-to-NULL rows are TODAY'S coherent records; ancestry includes
source within20 and follows no revisit alias. Missing/cyclic/foreign-team ancestry
refuses. The original source task/principal/SID selects exactly one native
manager_decision session_start under its closed owner grammar and the entire
bounded same-SID result inventory. Selected allocating INTEGER/full result must
match the source tuple and full closed workflow_start command/work radius, never
latest result/action label alone. Nonallocating NULL selection proves neither
original callback absence nor absence of a later callback. Current assignment/SID,
tracker reconstruction, source admission/claim/current policy are separate facts.

Caller independently admits complete native/J structure and holds one raw-value
connection/transaction/lock; fixed parameterized SELECTs only, no await/transaction
control/DTO/default/converter. Original typeof/native byte gates precede fetch,
including multibyte/NUL suffixes; each new native row's combined variable bytes
must fit remaining budget. Singleton keyset pages renew that gate; bounded task
uniqueness probe prevents LIMIT1 conflict masking. Every fetched/rejected/overflow
candidate shares8192 rows/16384 steps/16MiB raw bytes,1MiB per value,P64/setup1024/
source20. Nullable/empty/finite-REAL accounting does not weaken existing mandatory
setup/admission validation. This is not a total process/SQLite/copy memory bound.
Category-only coding_source_history_invalid returns no partial records/private SQL.

Fixed callback C0/source C1 captures and independent historical consumer remain
unimplemented here: independently admitted C0/C1 compare exactly25 named fields
and preserve both timestamp observations only at the first boundary; ALL27 C1=C2
must agree after preflight, so intervening heartbeat refuses with zero partial
coding allocation/debit/publication. Full27 replay and version-aware heartbeat
handoff remain mandatory after bootstrap. Complete captures/J/native/authority/
invocation/Pause/move/host/recovery/cutover consumers must precede EVERY producer.
No capture/native writer/public mount or live authority follows. F–J/refusal,
create-org I/document history, disabled K0 and unavailable K1 remain unchanged.
FIRST FULL independent reviewer, same-final-head scoped SOURCE QA, static local
all/hosted lint, normal hooks/nonforce push and manager guarded merge remain.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; all SQL/parser/
product/native/security/negative/mutation/repetition/executable proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Root retains K2a2–K6 and operational acceptance.
