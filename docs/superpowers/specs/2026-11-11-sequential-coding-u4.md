# Sequential Coding U4: task-owned repository resolution

2026-11-11; THR-336 / ROOT TASK-15144, maker TASK-15145. This specifies the
whole re-scoped U4. U4a supplies inert records/readers and an unmounted preflight;
U4b1 attaches durable business holds; U4b2 attaches immutable subject binding
and the resolve-only/editing boundary. TASK-15167 / ROOT TASK-15165 attaches U4b3
code-change declaration composition and downstream current-authority disclosure checks. Source delivery and static checks do not establish runtime acceptance.

## Authority and scope

R-M2-1 (`engineering_manager/output/TASK-15144/r-m2-1-u4-u9-rescope.md`, SHA256
`49d6cb83a575ae5e15b9b5cb6415371052cc66d54331cb15e3f238ddc01c153d`) controls
interpretation of plan rev3 U4 (SHA256
`adb23e805373d0694c4ad79f1847c50d5f7fabcb20fb193cb110f6dd6cee06e7`).
PRD v1.0.3 (SHA256
`8bdc05760d1cd2cac8be6bbd0574f2ebea45ff9314587e86f2bf54a6ca6feba5`),
F4/F5/F8 and AC4/AC8/AC9, supersedes the former four-role checkpoint.
Founder THR-336 msg85 authorizes U1–U9 source; msg102 narrows F4 and selects
the 48 KiB JSON limit; msg113 ratifies F-1=(i). The fresh fetched source base is
`6bf58e941cc1c1890b6e2f0410d5387e91642b83`.

Developer work starts **resolve-only** inside its existing node task. There is
no discovery node or separate task chain. Resolve actual repository/origin,
base and authorized scope before editing; verify supported **SHA1** object
format first. Unsupported format or scope holds editing immediately. F8's
“Unsupported repository format” belongs to the accountable manager; changing
agents cannot fix format. A resolved subject is fixed for the run: different
repository/origin/base or expanded scope requires a separately authorized new
run. Corrections change head/content epoch, never the original change subject.

The runtime four-role effective-duty-access checkpoint, observation adapters,
edit-readiness hook and readiness labels are removed. Ordinary role eligibility
remains. Eligibility/configuration/receipts establish no effective-access grant,
credential, OS containment or lasting permission. ACCESS-1 below separates
daemon current-authority rechecks from agents’ own effective-access operations
before later disclosure, publication and action. U5 still requires
independent exact-subject APPROVE and QA PASS even when graph data omits/reroutes
those nodes, plus selected live Final-manager-only local merge. Daemon performs
zero merges. Required selected-head checks remain Source Boundary Lint and
Web Lint (Node 24).

## Verified seams and caller inventory

| Owner / symbols | Current callers / reuse and U4 responsibility |
| --- | --- |
| `sequential_repository_records.py`: request, subject, cause, receipt, `read_repository_preflight` | U4a closed immutable models and canonical syntax reader. U4b2 preflight binding and subject owner consume these records; no public adapter. Saved parsing authenticates no issuer or permission. |
| `sequential_repository_preflight.py`: `SequentialRepositoryPreflight.resolve`, `_selected`, `_observe`, `_repository_layout`, `_git` | U4a synchronous worker-thread observation; U4b2 internal bind reuses selected ownership, reobserves outside transaction, then commits immutable evidence/Held. No OrgState registration, route, CLI, native-preparation or progression caller. U7 owns transport. |
| `repository_artifacts.py`: `_oid`, `RepositoryId`, `Component` | Reuse SHA1 OID syntax and existing org/key/origin-derived identity. Its saved presentation family also supports SHA256; that does not enable SHA256 for sequential Coding. `WorkflowRepositoryArtifactService.resolve` remains unavailable. |
| `repository_artifact_objects.py`: `_Budget`, `_directory`, `_regular`, `_absent`, `_names`, `_same`, `_config`, `_git_environment` | Reuse bounded no-follow metadata reads, closed clone-config parsing and isolated Git environment. Do not call `_resolve_objects`, `_build_resolution`, `_store` or adopt K1/K2 authority as sequential authority. |
| `coding_saved_records.py`: `_paths` (uses `_path`) | Reuse the retained 64-path, relative-prefix grammar (`.` supported), not its SHA256-capable grant/setup/source grammars. No old Coding record is rewritten. |
| `_coding_source_admission.py`: `source_storage` | Inspected, not reused as admission: it joins old mapping/adoption/source-capability provenance and verifies origin/object-format radius. Those producers cannot authorize a new sequential subject. |
| `SequentialDispatcher._entry`, `_on_loop`; `_native_task`, `_discover_result`, `_cancelled` | U4a reuses selected ownership, native task/SID, no-completion and cancellation checks. `_entry` is an existing compiler seam, not a new full-history scan. |
| `WorkflowAuthorityCoordinator.capture_admission`, `admission_writer`; `_bound_agents`, `parse_start` | Fresh capture precedes coroutine locks; short owned scopes validate actual fixed role bindings and current eligibility, never effective duty access. Revalidate again after observation. No Git/filesystem/network work inside writer ownership. |
| `SequentialDispatcher.node_instruction`, `_compose`, `_advance` | Existing node instruction caller is orchestrator execution; `_advance` is called only from positive `outer_settled`. U4b2 adds the code-change resolve-only/committed-subject supplement. U4b3 retains selected-input authorization and code-change composition. Preserve the document-only branch and existing document witness policy. Avoid a module-time import cycle: U4a imports dispatcher helpers; future dispatcher composition must use a callable-local service import or factor the existing owner seam. |
| `sequential_engine.accept_result_uncommitted`, `transition_uncommitted`, `sequential_return` | Existing dispatcher lifecycle callers; U4b retains genuine native quartet, result/output limits, CAS/replay and post-COMMIT publication. No old Coding progression call. |
| `sequential_history._auxiliary` / event replay; `sequential_run_validation._validate_lifecycle` | Existing complete and selected reader owners. U4b1/U4b2 recognize/validate hold and subject evidence before producer attachment; no weakened whole-layout comparison or frozen history rewrite. |
| `SequentialDispatcher._repository_held`, `callback_uncommitted`, `terminal`, `outer_settled` | U4b2 repair authenticates only the repository-Held family for an original negative BLOCK callback. Existing completion writer, native terminal and TaskInvocation outer callers retain ordering, authority, replay and cancellation fences; no sequential acceptance or advance. |
| `task_pause.settle_live`, `run_step._run_step_owned` tail | Existing native settlement callers. U4b does not change their ordering or bytes to handle business refusal. |

## U4a receipt and observation boundary (this PR)

The request declares repo key, full base commit OID and sorted distinct relative
scope prefixes. Scope is resolved by the actual Developer within its task's
authorized brief; this declaration is not machine proof of a grant. Supported
scope uses retained path syntax, 1–64 prefixes and at most 8192 UTF8 path bytes.
The serialized subject is additionally limited to 12000 bytes so JSON escaping
cannot overrun the receipt. The original request digest preserves attribution
even when scope is refused.
No config/setup repository selector is added.

Before probing, `_selected` requires one original live sequential invocation
owned by the attached dispatcher, actual task/principal/SID, active run and
selected running code-change visit, no native completion/accepted result,
uncancelled ancestry and current team-move/authority eligibility. The repository
key selects only this principal's current configured origin, under its native
`workspaces/<principal>/repos/<key>` path. Unknown repository/scope is Held.
This is selected task-session service authentication, not a public API promise.

Observation reads only ordinary clone metadata: no-follow repository/`.git`
directories and bounded config; exact observed/configured origin equality; Git
`rev-parse --show-object-format=storage`; then SHA1/full OID validation and
`rev-parse --verify <full-oid>^{commit}`. No edits, fetch, refs selected by name,
source-content read, helpers or network. The original five-second repository
budget covers both metadata calls and bookends; Git stderr stays private.
SHA256 yields `unsupported_repository_format`; unsupported path/base/scope yields
`unsupported_repository_scope`; missing/unreadable/unsupported clone metadata
yields `repository_unavailable`; observed origin/config/directory movement yields
`repository_changed`. This subset supports ordinary managed clones with local
bare origins; linked `.git` files and bare editable targets remain unavailable.
Filesystem bookends are observations, not a racing-rename or hostile-shell fence.

Before either Git command and again at close, `_repository_layout` refuses
`commondir`, `gitdir`, object-store `info/alternates` and `http-alternates`,
`info/grafts`, loose `refs/replace` and packed replacement refs. The literal
`.git` must be a no-follow directory, so gitfiles and linked worktrees refuse.
The bounded ordinary two-level object-directory metadata inspection rejects
symlinked directories/files and nested or special entries; no object contents
are read. Unsupported topology returns existing `repository_unavailable`, never
a resolved subject. Closed `_config` rejects include/includeIf and helper
configuration; `_git_environment` replaces inheritance with fixed isolation
values, never caller Git directory/common-directory/object/alternate/config
overrides. `--no-replace-objects` remains defense in depth, not permission to
accept replacement refs. Assumption: shallow clones are allowed, since shallow
history does not redirect repository identity or storage. Closing checks repeat
all these absences/topology checks as well as the config/directory bookends.

After probing, a second fresh authority/task/session selection must match the
original context and configured origin before any private metadata is returned.
Authority/session refusal returns no receipt/disclosure. A legitimate observation
failure returns a canonical `workflow-sequential-repository-preflight@1` Held
receipt with null subject, finite cause, accountable-manager next owner and
no-automatic-retry action. Successful receipts carry the actual org/run/visit/
task/principal/SID, definition/start/request/authority pins, origin digest and
org/key/origin-derived repository ID, base, scope and `object_format=sha1`.
Raw origins (which can contain secrets), local paths and Git diagnostics are
excluded. Origin digest is exact-byte identity, not a credential or access proof.

The historical reader accepts only canonical duplicate-free closed UTF8 JSON,
strict types, matching identity and disposition, and at most 16384 bytes. Neither
constructing nor reading this family authenticates an observation.

**U4a has no evidence DML, run binding or lifecycle state writer.** It produces
receipt bytes only. A Held receipt is not a claim that the database run is Held.
No existing Coding producer consumes it; document workflows, Disabled admission,
public start, native launch and settlement behavior remain unchanged. This is
the deliberate records/reader/preflight split, not whole-U4 acceptance.

## U4b immutable subject and code-change composition

Attach only after reader-first independent review. The first selected Developer
remains resolve-only until its genuine task-session receipt is durably bound.
Use the existing append-only `workflow_seq_evidence` **preflight** slot at run
scope for the single successful change subject, including original issuer and
pins. Keep nullable native provenance columns NULL before a genuine result;
never manufacture an INTEGER result. An explicit bounded reader validates the
closed receipt, digest, original visit/native identity and run/pin relations.
The selected writer revalidates current authority/session and exact current
repository observation before COMMIT. No awaited filesystem work inside writer.
Only positive subject COMMIT can release the selected Developer's editing phase.
This is a repository-format/subject boundary, not the removed duty checkpoint.

On repeated request, compare exact original repository/origin/base/scope. Exact
replay reuses original bytes/IDs; another target or scope expansion holds with
cause and requires a new authorized run. A narrowed actual diff may fit the
original authorized prefixes without rewriting them. Correction visits inherit
the same subject and lower original limits; no latest-repository adoption.

Code-change composition and downstream disclosure now follow ACCESS-1 below.
U5 retains independent exact-subject review/QA capability and selected-manager
merge; U7 retains public task-session transport. There is no new access observer.

No new table/column/index is required by the proposed subject/hold evidence
encoding. If implementation reveals that existing closed readers cannot preserve
these invariants additively, stop and return the exact schema need to ROOT.
Any subsequently approved schema delta must follow
`workflow_sequential_schema.py` / `install_workflow_sequential_schema.py` patterns;
never put an index on a new column into the fresh startup executescript. Install/
migration stays founder-reserved, source only; no live operation follows.

### U4b2 source implementation (TASK-15159)

`SequentialRepositoryPreflight.bind` is the explicit internal worker-thread
writer. It resolves a fresh receipt, repeats the exact clone observation outside
writer ownership, then reselects the current live task/SID, authority/configuration
and run pins in the short admission transaction. There is no Git/filesystem work
or await inside that transaction. Observation bookends remain cooperative, not a
racing-rename fence. A changed selection/authority/observation refuses without a
subject write. No public task-session command or OrgState service mount is added;
U7 owns transport. At U4b2 delivery successful code-change output remained
unavailable; U4b3 below attaches declaration composition.

`sequential_repository_subject.py` owns bounded canonical subject validation.
Both complete `_auxiliary` and selected `_validate_lifecycle` recognize the
run-level `preflight` slot before producer attachment. Exactly one deterministic
run ID stores the successful U4a receipt, including its original issuer/authority
pins. The table's existing all-or-none native-provenance CHECK is retained:
visit_id and task/principal/SID/result columns stay NULL in this run-level row;
original visit/task/principal/SID are authenticated from receipt bytes, the pinned
run/definition and native parent/assigned-agent/session relations. The original
issuer must be the first code-change visit. No schema or historical bytes change.

Exact replay returns the original canonical bytes/ID, even under newer current
authority or a correction visit. A narrowed requested scope reuses the original
prefixes without rewriting them; it grants no broader scope. A different
repository/origin/base or expanded scope commits a finite non-authorizing Held
observation with the accountable manager and new-authorized-run action. Failed
format/scope/metadata preflight can likewise hold before any subject exists.
This distinct `workflow-sequential-repository-hold@1` family reuses U4b1's
observation/Held reader seam, without inventing successful terminal proof or an
INTEGER result. All native provenance columns stay NULL. Event count/CAS/last
event remain unchanged, no output/successor is admitted, and cancellation
is the only later lifecycle event. Exact same-session Held redelivery acknowledges the
original observation without a new probe, permission or retry.

The authenticated original live task/principal/SID may still commit its canonical
negative `BLOCK` callback under current authority, with empty content/claims and causal
findings. Success-shaped callbacks and foreign sessions refuse; exact full-payload
replay retains the one original INTEGER result. Original safe terminal/positive outer
settlement drains without sequential acceptance: run/visit stay Held, accepted result
stays NULL, and no output, successor, editing release, resume or retry follows. Native
completed/failed status is retained; a blocked callback keeps its blocked
result/classification and closes the native task as failed after safe terminal proof.
Cancellation wins over fresh callbacks/settlement; an already committed callback may
only replay unchanged. The immutable subject, hold bytes/cause and event prefix are
preserved.

`node_instruction` supplies a code-change domain supplement: resolve-only with
no committed subject, editing only under the original committed subject. Binding
returns only after positive COMMIT; insert/update/COMMIT failure rolls back and
releases no editing phase. This is the subject boundary, not a four-role access
checkpoint or permission-enforcement change. Scope remains the selected
Developer's declaration under its authorized brief, not machine proof of a grant.
Downstream declaration composition/disclosure is attached by U4b3 below;
review/QA capability and merge remain U5; correction visits cannot adopt a
latest repository or raise original run limits.

Disposable scratch diagnostics observe local SHA1 clone binding, complete and
selected readers, original-byte replay, narrowing, target/base/scope expansion
Held, pre/post-COMMIT phase, stale selection/authority refusal and failed-COMMIT
rollback. They use synthetic pre-result native/live/authority scaffolding derived
from a retained M1 DB copy; they are not genuine native admission/callback/host
or V3 acceptance. Full correction progression and racing filesystem changes remain
NOT RUN. No repository tests are added or changed.

## ACCESS-1 / U4b3 attached source (TASK-15167, ROOT TASK-15165)

Founder THR-336 msg131 (2026-11-11T01:36Z), applying msg129's principle that
runtime orchestrates repository work, is controlling over PRD v1.0.3 lines
94/96/150/209, AC4/AC6/AC7 and earlier U4 access-observer wording. Product lead's
PRD v1.0.4 update is tracked in THR-351; this source cites msg131 directly and
does not claim that publication has landed.

Before each later disclosure, publication or action, the daemon rechecks only
current role eligibility, original fixed role binding, active executable
principal and unchanged committed repository/origin/base/scope. It performs no
GitHub/network I/O, fetch, new credential operation, permission change or
per-principal access observation. The U4a/U4b2 local SHA1/origin/base metadata
preflight is unchanged. Agents share one OS user and one gh credential, so
per-agent effective repository access is not independently observable by the
runtime. Selected reviewers and QA prove access by their own exact-head fetch;
the final manager proves its action access by the expected-head non-admin
guarded merge (U5). A failed required operation means BLOCK then Held, retaining
the distinction between denied and unverified. Earlier preflight is no grant.

`SequentialDispatcher._advance` still starts only after genuine callback/native
terminal/positive outer settlement COMMIT. For a code-change node it authenticates
the original native task/principal/SID/INTEGER and current binding context, then
`_compose` accepts a closed canonical declaration of at most 49152 bytes:

```json
{"epoch":1,"format":"workflow-sequential-code-change@1","head":"<full lowercase 40-hex SHA1>","pr":{"number":1,"repository":"owner/repo","url":"https://github.com/owner/repo/pull/1"},"subject":{"base":"<full lowercase 40-hex SHA1>","object_format":"sha1","origin_digest":"<original SHA256>","repo_key":"<original key>","repository_id":"<original repository ID>","scope":["<original prefix>"]}}
```

The placeholders above describe the shape, not executable evidence. Subject
must equal the complete original subject, including unchanged scope prefixes;
epoch must equal the current visit epoch. PR repository/positive INTEGER/URL
must be well formed and internally consistent. These are untrusted declarations:
the daemon does not verify remote repository/PR association, head existence,
PR current head or diff scope. Those obligations belong to the downstream
agents' own exact-head operations, not the syntactic checks. Nonempty capability
claims always refuse; they cannot issue a capability.

`sequential_coding_output` owns declaration validation and deterministic witness
preimages binding original run/definition/start/subject, epoch, native quartet,
carrier/input-selection/content digests, PR/head declarations, original sorted
producer/current-consumer recipients and terminal/outer digests. Ordinary result
envelopes retain their schema. The `repository-pre-edit@1` check refers to the
authenticated saved metadata preflight; `code-change@1` establishes declaration
provenance, not semantic correctness, effective access or U5 review/QA/merge.
No new evidence table, column or index is needed.

Malformed declaration/PR/OID, changed subject/epoch, absent subject, claims,
oversized JSON or current-authority failure causes zero accepted outputs/checks.
After rollback, U4b1 records durable Held with finite causes, adding
`sequential_code_change_invalid`, `sequential_repository_subject_changed`,
`sequential_repository_subject_unverified`, `sequential_authority_denied` and
`sequential_authority_unverified`. Missing/inactive/ineligible fixed principals
are denied; unavailable/stale authority is unverified. Invalid native identities,
corrupt history, budgets and persistence errors retain their existing refusals;
a failed COMMIT cannot report Held or publish a successor. Original settlement
owners/bytes and Pause-tail ordering are unchanged.

At **every** Coding `node_instruction`, fresh authority is validated in the owned
admission scope before any selected input bytes return. The addressed principal,
exact selected ref/native provenance/digest and original recipient set must
match; selected code-change declarations must still match the committed subject.
A denied/unverified authority check returns a typed block, with no disclosure,
recipient/binding/selection update or retry. U5 outputs remain unavailable and
are not disclosed by this slice. Document composition/witness policy is unchanged.
Historical subject/hold reads grant no new publication permission.

Disposable diagnostics use derivative retained-M1 SQLite fixtures and synthetic
authority/live-session/native-proof scaffolding. They observe ordinary Coding
composition/transition, finite Held cases, downstream blocks, document B/H bytes
and failed-COMMIT rollback. They are not genuine callback/native/host/provider,
review/QA, full correction progression or V3 acceptance. Repository tests are
unchanged; Python units are RETIRED, general integration SKIPPED24342, automated
merge checks lint/static only. See TASK-15167's diagnostic receipt for actual cases.

## U4b F-M1-CODING-HOLD-TAIL: durable post-settlement Held

Observed M1 failure: `outer_settled` (~510) → `_advance` → `_compose` (~524)
raises `SequentialRefusal('sequential_output_access_unavailable')` at
`document_only`. It escapes `task_pause.settle_live`; `run_step` (~1260–1270)
raises `ExceptionGroup('task Pause tail settlement failed')`. Native/outer proof
was already committed, but run remains active, visit running, accepted result
NULL and the native close barrier is retained. Preserve this failed evidence;
U4a does not repair it.

The **load-bearing order remains**: genuine callback/native result COMMIT →
native terminal observation/settlement → outer cleanup and original Pause close
→ original team-move release and tracker-clear observation → sequential positive
outer-evidence **COMMIT** → fresh authority capture → compose/accept/transition
in the sequential writer → post-COMMIT queue/caller publication. In `settle_live`
the existing coding tail observer precedes the sequential tail observer. The
`run_step` finally loop still calls `settle_live` and aggregates real settlement
errors. No outer/native settlement or Pause-tail ordering changes to fix Coding.

U4b must classify expected business refusals from advance/compose into a finite
typed business-hold cause, including unsupported repository/output/access and
malformed/oversized business output where applicable. Never blanket-catch
`Exception`/`BaseException`, swallow corruption/SQLite/ownership failures or turn
an uncertain native settlement into a successful hold. A refused sequential
transaction rolls back first; original callback/terminal/positive outer commits
remain authentic. Catch the classified refusal only **after** that rollback and
after the original positive outer COMMIT, at the sequential-owned boundary below
`outer_settled`; it must not escape through Pause tail as an expected refusal.

A short independent hold writer reselects original run/visit/task/SID/INTEGER and
validates unchanged selected CAS/last-event, original successful terminal,
positive outer and matching callback identity. Current cancellation or a settled
competing transition wins; there is no repair/replay of uncertain effects.
Recording refusal grants no authority, so a current-access denial must not make
its own cause impossible to record. Use native DB/selected ownership for this
non-authorizing writer, not a fabricated successful admission capture.

Atomically append a deterministic, closed business-hold **observation** in existing
`workflow_seq_evidence` (run/visit/result-derived identity) and set the selected
visit/run to `held`. The cause carries affected node/role/principal, finite reason,
original source/proof digests, next owner and supported action/limitation. Keep
original result attribution, accepted-result NULL on pre-accept refusal, no output,
success check, successor, outbox, caller return, merge or automatic retry. Leave
event_count/CAS/last-event unchanged: this observation is a refusal overlay, not
a fabricated reducer outcome or transition event. Complete and selected readers
must authenticate this exact observation against the otherwise active event
prefix and original committed proofs before recognizing the Held overlay.
Unknown/mismatched/conflicting observations still refuse. This is an additive
evidence reader extension, never a historical transition rewrite.

Hold observation and both state updates validate and COMMIT together with exact
row-count/CAS checks. Exact redelivery acknowledges the same cause/IDs without
advancing or emitting a second hold. Persistence/validation failure remains a
real error and cannot be reported as durable Held. After a successful hold
COMMIT the sequential observer returns normally; outer/native success stays true,
and process/history/task views can explain the Held cause and next owner.
Lifecycle resume/effect recovery is outside this unit.

### U4b1 source implementation (TASK-15150)

`sequential_business_hold.py` defines the finite causes
`sequential_output_access_unavailable` (including repository/mixed output),
`sequential_capability_unavailable`, `sequential_carrier_invalid`,
`sequential_carrier_limit_exceeded` and `sequential_output_limit_exceeded`.
Only these typed advance refusals are caught, after the admission writer and
its rollback have exited, before any post-COMMIT publication. Identity,
corruption, SQLite, ownership and unclassified authority failures propagate.

The independent writer uses native DB ownership without an admission capture.
It reselects the original task/SID/INTEGER and successful terminal/positive
outer proofs, authenticates the current selected prefix and compares the
original CAS/event/visit pins. Current cancellation or an authenticated later
transition wins. A result-derived deterministic observation and both Held
updates commit atomically with exact row counts. The observation carries node,
role, principal, callback/source/proof digests, accountable-manager next owner
and no-automatic-retry/resume-unavailable limitation. Accepted result stays NULL;
CAS/event bytes/count/last-event and all prior proofs stay unchanged.

Both complete and selected readers authenticate the closed observation against
the selecting event and original native/terminal/outer proofs. Only a later
cancellation may follow it; unknown, mismatched or conflicting observations
refuse. Exact original outer redelivery acknowledges the saved IDs/cause without
fresh access or advancement. Failed persistence propagates and rolls back.
Document composition, size limits, witnesses, callback/agent instructions and
native/Pause tail ordering are unchanged. No schema change or resume is added.

## Acceptance obligations and delivery evidence

| Case | Required observation and current source/evidence status |
| --- | --- |
| AC4 two targets | Two separately authorized requests resolve two different local SHA1 repositories/bare origins under identical definition/settings/bindings. Distinct immutable subjects, no discovery node or config edit. NOT RUN. |
| F4 SHA1/scope | SHA1 verified before any first edit; SHA256, invalid scope/base, origin drift, unknown repo and unavailable metadata produce Held with cause and no edit. U4b2 subject boundary attached; disposable scope/COMMIT phase diagnostics observed. Native first-edit ordering NOT RUN. |
| Subject/replay | Exact receipt replay retains original IDs/bytes; changed target/base/origin or expansion cannot mutate the subject; correction head/epoch retains base/scope. U4b2 attached; disposable bind/readers/replay/target/base/expansion-Held observed. Genuine correction progression NOT RUN. |
| Later access loss | ACCESS-1 msg131: daemon disclosure rechecks current authority and unchanged subject; denied/unverified blocks leave bindings/selection/recipients unchanged (U4b3 attached; synthetic diagnostics only). Effective access is proven by selected agents’ own exact-head operations and manager guarded merge (U5); those real operations and later-action proof remain NOT RUN. |
| AC8 | Wrong task/SID/result/subject and duplicate results preserve original evidence/budgets; oversized JSON Held, unsaved, unsuccessful; no second allocation. Existing 49152-byte cap retained. U4b3 synthetic wrong-subject/OID/PR/claims/oversize/identity diagnostics observed; genuine native/remote proof NOT RUN. |
| F8/AC9 hold tail | Force an expected post-positive-outer business refusal: one durable Held visit/run/cause, original proofs preserved, zero successors/outputs and no Pause-tail ExceptionGroup. Include replay, cancellation/transition race and failing hold persistence without false Held claim. U4b1 source attached. Disposable retained-M1 snapshot diagnostic observes durable Held, both readers, exact outer replay without fresh authority, cancellation winning and persistence failure propagation. Full native Pause-tail execution, genuine concurrent races and V3 are NOT RUN. |
| Document compatibility | Document composition, limits, recipient witnesses and native order remain identical; no default admission/public-start change. U4b3 compares document content/envelope/witness bytes at B/H in disposable diagnostics; genuine document execution/native order NOT RUN. |

No tests are added or changed here. Future executable case authoring must pass
test-authoring-evidence-gate's four answers (observable contract, credible
regression, nearest coverage/gap, no test-only seam), duplication check and honest
fail/pass evidence or unavailable proof. Python units are RETIRED; general
integration is SKIPPED under THR-243 seq42. No ad hoc Product E2E or V3 execution
is authorized by this source leg. Independent full code review, honest scoped QA,
normal hooks/nonforce push and ROOT guarded merge remain required at final head.

Doc parity through U4b2: this spec, reusable-workflows contract, task-orchestration
contract and bundled start-task skill describe the internal binding and phase boundary. No public
route/schema/CLI/TS/native change, permission/allow-rule/credential/grant change,
role assignment, admission/settings/live migration, deployment or restart. U4b
must renew parity for each attached behavior and leave reserved operations held.
