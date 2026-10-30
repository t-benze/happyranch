# Coding workflow contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

## Creation-time ownership compatibility

THR320 seq432/433 and seq436/437 add the org-owned companion task mark and
scalar binding view described in [task orchestration](task-orchestration.md#creation-time-task-ownership-thr320-seq432433-seq436437).
They initialize once from complete validated authoritative bindings before
producer attachment; a missing record never means ordinary. Each author,
reviewer, revision, coding stage/successor and source/carrier writer persists its
actual mark in its existing graph transaction. Full semantic/authority/native
validation remains at actual workflow boundaries. Historical task snapshots,
old layouts/pins/journals, installed owner indexes and complete independent
schema comparisons remain intact. Marked storage requires compatible readers;
this source change authorizes no live migration, enablement or workflow start.

## Current built-in coding definition — ROOT13509 / THR320seq304

Normally admitted orgs receive the unchanged packaged coding v1 definition
without a human import. Normal org recovery constructs the private immutable
resource observation outside locks and registers only after the actual move,
Pause, cutover, naming and authority safety tail. With serving-before-recovery,
registration belongs to the deferred recovery owner, never early serving.
Generic Database, schema/reference constructors and SELECT readers never register.

System registration appends exactly identity, draft, version and identity-version
rows, with canonical `workflow-coding-builtin-registration@1` provenance, actual
org and one UTC timestamp. It creates no human operation/admission/receipt,
settings, mapping, grant, task or run. The unchanged six-row human import family
and original receipts remain literal. Template publisher is either the genuine
human map or `{kind:system,component:coding-builtin,provenance_format:workflow-coding-builtin-registration@1}`.
Human import GET remains404 `coding_release_import_not_found` for system origin;
fresh import POST is409 `coding_release_builtin_registered`, with zero human rows.
Complete typed release/queued/native readers accept legitimate bootstrap bindings
for either origin and reject orphan/extra/mixed/foreign/unsupported rows.

Existing valid human/system registration is a write-free no-op. New registration
uses the genuine native writer, publisher then DB lock, an owned transaction,
complete source/target validation and two separate attempt-local allowances.
The complete ordered native census, every suffix, historical/live-state visits
and fixed safety probes share 8192 acquired rows/16MiB/2097152 visits; native
payload/cell caps remain 16384 bytes. Release/source/expansion/target retain ONE
original 8192-row/16MiB/16384-visit budget. Neither allowance resets per phase.
Strict storage/UTF8/duplicate/org/task/agent/first-active/immutable/transition
checks and live/durable exclusions remain. Native reservation/settlement replay
is separate work; these caps guarantee no SQL-engine, RSS or wall-time bound.

Only registrar-owned pure refusals and the direct healthy `team_move_native_held`
refusal can defer optional registration. The latter requires the producer's
canonical operation identity, no cause/context/notes and the unchanged mounted
owner; the exception class or message alone is insufficient. Wrapped move schema,
complete history, scan-bound/deadline and SQL failures propagate unchanged, as do
uncertain origins and opaque shared allowance errors. Each native cursor owns
its consumer loop and explicit close. Safe preflight requires the exact body
refusal, positive original rollback, BOTH successful publisher/DB exits, identical usable connection,
no transaction and no secondary notes. Native entry is outside that interval.
After a durable reservation, those same proofs precede exact writer.validation
marking INSIDE native_writer; warning follows only its genuine successful exit,
settlement and original owner/registry disposition. Entry/no-yield, failed close,
rollback/outer exit/settlement or same-primary cleanup notes never mean no effect.
Active/unknown native owners defer, with one built-in registration-only exception
(Founder THR320seq477): after complete strict native-state validation, an `unknown`
`disk_sweep` record with empty agents AND teams may pass the historical-state gate
only when its writer key is absent from the SAME currently mounted
`admission.writers`. A present key whose value is false or None still refuses.
Actual mounted-owner identity and CURRENT writer reservations are revalidated at
all three safety calls: preflight, pre-write and pre-COMMIT. Every other current
writer, publisher/lease/profile operation and transitional cutover still refuses;
existing own/competing built-in registrar cases remain unchanged. The record stays
UNKNOWN; no process absence, settlement, cleanup, recovery or adoption is inferred.
Positive original COMMIT alone
publishes collected full-reader watermarks under the same DB lock. COMMIT and
post-COMMIT publication uncertainty propagate with actual effects retained;
no optional warning/retry follows. Resource absence defers availability with
bounded reason and resource/original safety-owner remedy, never storage repair
or import. Package loss or safe refusal does not require enabling coding.

Saved history, template reads, exact original replay and admitted continuation
remain package-independent. Fresh options/settings/mapping/start use requires the
captured exact packaged bytes; unchanged Disabled-retain preserves its accepted
mapping. Four registered rows require compatible readers even with no run;
no old-binary downgrade claim follows. SQL/DDL/reference digests stay unchanged.

First-use Settings binds available metadata only to a pristine zero-basis draft,
without a request, key or Adopt prerequisite. Select repositories/formats/four
eligible distinct org agents and Save; Admission starts Disabled. Dirty/stale
metadata still requires explicit adoption. Org/mount/options lifetimes preserve
original provider-owned pending/uncertain save fences; no automatic PUT occurs.
Guided human setup observes the saved template through read-only team/template
listing instead of importing; actual human originals get readback only.
The twelve-object loaded observer does not attest this registrar/resolver.
Source/static delivery is not deployment, loaded identity, settings persistence
or scratch acceptance. Those and ordinary coding admission remain root-owned.

## Current coding storage and setup — ROOT13150 / THR320seq261/267

Normal `OrgState.load` installs the unchanged four coding tables beside each
normally admitted org's supported document foundation. Fresh F plus coding is
one `Database.workflow_schema_transaction`; existing complete storage is
validation-only except for the retained org-owned installation of the complete
canonical `workflow_owner_*` index set after full source validation. Complete
unindexed and indexed layouts are both recognized; partial or noncanonical index
sets refuse. The owning writer validates the complete target before COMMIT.
Before any workflow DDL, the entire reserved coding family is observed. Partial,
extra, case-changed, wrong-kind or malformed objects and real shared dependency
failures refuse without repair or adoption. Body/interruption
and COMMIT failures preserve the primary cause and secondary cleanup errors;
uncertain COMMIT is not success or proof of absence. Audit-watermark advances
publish only after positive COMMIT under the same lock. Failed unpublished org
construction closes/refuses before attachment; later installers are separate.

Coding validates its canonical inventory and actual shared columns, keys, FKs,
owner histories, template/authority/native dependencies and Pause/move/naming
controls. Unrelated native ALTER chronology is not a coding-only whole-org SQL
prerequisite. Nullable submission/request FKs and nonNULL owner checks remain.
F/E/G/H plus coding keep their document identity; I plus coding retains literal
J. Complete old F/E/G/H/I/J histories, original import/mapping/settings/run pins,
INTEGER results, SIDs and UNKNOWN effects stay unchanged. Every cold/metadata/
recovery/post-write/return/cancellation path retains the same complete readers
and one original cumulative row/byte/step allowance, with no recursive admission.
Only a successful same-snapshot canonical reserved-family observation makes
absent E draft/H handoff/I revision exclusions empty; present families retain
full validation and actual conflict queries. SQL/read/lock errors never mean
absence. Mandatory F request bridges and other owners remain required.

Historical F/E whole-reference constructors use literal foundation primitives,
not operational startup. Independent coding companions and separate coding and
owner-index cache dimensions reach both generic whole-reference consumers. Raw
whole nonNULL SQL comparison stays unfiltered; legacy authority keeps its four
histories even after workflow's fifth-history cache fill, and authority-v2 stays
observed-only. No reference preimage or saved pin is rewritten. Generic Database
and runtime-audit remain workflow-free. Even empty F+coding requires compatible
code at downgrade; document blockers and returned/live/held drain rules remain.

The existing human setup coordinator retains present maintenance, backup,
source/UI/loaded-object and original operation/receipt custody. Its normal run
no longer invokes F/E→G→H→I→J migrations. The original supported deployment/start
owns inert coding initialization; post-start revalidation requires complete
coding storage on each normally admitted selected-runtime org, with document
identity unchanged except I+coding=J. Existing explicit document migration tools
remain separate. No old-folder or historical uncertainty settlement is a new
initialization prerequisite. Missing settings projects Disabled; installation
writes no settings, import, roles, grants or run and preserves Explicit settings.
The twelve selected loaded-object producer/consumer bindings remain matched;
health or disk HEAD alone is not loaded acceptance.

This source contract supersedes earlier coding-only J/whole-org-equality and
mandatory offline-chain instructions below, preserving attributed old evidence.
Human deployment/restart, Founder settings, independently loaded
readiness and one genuine four-role scratch run remain separate root-owned
acceptance. Source/static checks prove no workflow behavior. Python units are
RETIRED; integration including callback/lifespan/SessionTracker is SKIPPED24342;
no ad-hoc Product E2E or new test/build merge gate follows.

## Current explicit coding execution and caller return — ROOT13015

Founder THR320 seq194/196/225 and manager TASK-13015 authorize the bounded
coding integration. This current contract supersedes unconditional-disabled
instructions in the historical K3/R2/revision/source checkpoints below; their
original observations, failures, approvals and immutable encodings remain history.

New starts require the mounted native queue/host/runtime, complete coding/shared storage and registered release,
current canonical settings with Admission Explicit and matching settings/release/
mapping pins, and the existing current-authority/native admission predicates.
Missing, malformed or Disabled settings refuse before callback persistence, spend
or allocation. Disabled remains the default; ordinary routing remains legacy.
Literal start@1 stays readable but is not a fresh-start path. Admitted start@2
work retains its original pins across later disable/default changes and still
requires current selected-role/repository authority, Pause/move and native proof.

Source capability projects readiness `{enabled:true,reason:coding_engine_available}`;
this is neither per-org admission nor installed/loaded/operational acceptance.
The strict historical false/downstream-unavailable pair remains readable. Settings
copy in both locales distinguishes capability, admission and saving from a run.

The existing four distinct actual roles and their genuine task/SID/INTEGER results
progress through development, independent review, QA and final live manager.
The selected final manager alone performs the existing local guarded expected-head,
non-admin action and independent actual MERGED/commit/aware-time observation before
its own callback. No daemon PR I/O, job executor or callback substitution is added.
Fresh revisions and the bounded no-action manager successor use the same return.

After manager_completed records merged work AND every original stage has successful
host/quiescence and positive outer settlement, additive event@9/return@1 completes
the original source and structural carrier in that SAME transaction. Their final
summary/directory and note derive from the genuine manager result; source SID and
all original results remain, and the carrier gets no SID/result. The independent
reader derives every changed task field; rollback/full target validation uses the
original aggregate allowance. Literal old complete/held histories are not adopted
or rewritten. Returned metadata uses run@8, retaining old run@1–7 readers.

Only a committed authenticated return may notify the ordinary parent and publish
the existing terminal task history/thread tail. Runtime wiring rediscovers returned
sources omitted from nonterminal scans, using complete readers and the native
stored thread marker to avoid a second notification. A partial thread tail stays
with its original owner. Coding leaves/carriers never enter legacy allocation.
The existing root terminal-worktree tail remains separately attributable.
After return, ordinary callers own their later state and later allocations;
original complete captures and stable ancestry identity remain validated, while
all coding-owned current task rows still compare exactly. Returned graphs no
longer block drain; J and all retained new bytes still require a compatible reader.

A later ordinary ancestor cascade retains a genuinely returned coding graph as
immutable history, including through terminal ordinary dependency paths and
alongside fresh or already cancelled graphs. Full return replay, every original
successful/quiescent native and positive outer closure, terminal owned rows and
original capture ordering are required; a complete label alone never qualifies.
Only reachable live ordinary tasks receive their sole cancellation overlay;
only fresh coding graphs receive a fence. Returned source/carrier/stage/result/
event/PR/merge/owner bytes receive no write or control. Batches containing a
returned graph use closed cancellation command/event@2, or ordinary-cancellation@2
when no fresh graph exists; retained anchors select the original authenticated
return or cancellation event. Literal @1 retained anchors still require prior cancellation.
Independent readers reconstruct membership, every anchor and audit/causal order,
then compare full current SQL within the original transaction and ONE aggregate
budget before COMMIT. Missing return, held/unknown/unsettled/native-uncertain work,
hidden live descendants, drift or malformed membership/order refuse and roll back.
Point stops of coding-dependent ordinary ancestors still refuse before writes.

Native deferred recovery remains unavailable. Source/static delivery is not native
or remote workflow acceptance. No deployment, host maintenance/restart, migration,
import, role assignment, activation or historical settlement follows. Benze or the
designated host operator owns existing guided deployment/setup; the human Founder
owns original import/settings. First genuine scratch through four actual roles
and actual merge remains required before ordinary routing. Revision/control,
cross-team persistence, two-org/default/override/backup/rollback remain open until
demonstrated. Python units RETIRED; integration INCLUDING callback/lifespan/
SessionTracker SKIPPED24342; Swift UNEXECUTED/FOUNDER-EXEMPTED/NONBLOCKING32093.


<!-- K3a result-owned queued bootstrap: TASK12003 / ROOT10693 step87 -->
## Result-owned coding start and queued ownership (K3a)

The new closed manager decision is exactly `{action:workflow_start,workflow:COMMAND}`.
`COMMAND` is canonical `workflow-coding-start@1` with exactly
`format,operation_key,release,mapping,work,implementer_override`; release and mapping
are the existing exact pins. Work is exactly `brief,repository_id,object_format,base,paths`:
nonempty brief at most16384 UTF8 bytes, full lowercase SHA1/SHA256 base, and1..64
validated relative selectors. There is no developer head before genuine output.
`happyranch workflows coding run-start --from-file ABS --emit-decision` validates
and prints only this decision for the initiating manager's own final callback.
It contacts no daemon and allocates nothing. No fresh HTTP allocation route exists.

The actual callback and result-action entries select this family before v2
spend/claim or ordinary decision effects. Fresh callback admission refuses before
persisting an unsupported start result, including on legacy I. Its production owner refuses
`coding_downstream_unavailable` before claim consumption or allocation. Readiness
is always false in K3a; there is no caller/config readiness Boolean. Behind that
boundary, the real single admission transaction validates the manager's own full
INTEGER callback, actual ordinary-purpose TaskInvocation/SID and complete RetryClaim,
current policy/Pause/move/ancestry, imported release, pinned role generation,
implementer-only override, four current independent eligible principals, existing
repository scopes, capacity and lower source limits. Existing v2 uncommitted
spend/claim/ack methods keep their native evidence and CAS semantics inside that
same graph transaction. A still-active historical v1 continuation envelope
refuses as unavailable; its self-committing spender is never bypassed or composed
inside bootstrap. No self-committing delegate or task helper is composed.

Bootstrap inserts pinned authorization/binding/context/activation/instance, a
structural nonexecutable carrier parented to the authentic source, one pending
development leaf parented to the carrier, a queued attempt and `run_started`.
Instance root is the carrier; Pause root remains the actual top source ancestor.
CAS starts at1/current_revision0/target_revision1. Source waits delegated;
its original parent/counters/SID remain. Immediate cyclic FKs use uncommitted null
run pointers only until the real attempt/event are inserted and both pointers set.
Full typed native/J/history/FK/schema closure precedes COMMIT. Failure rolls back
all rows, pointers and claim writes; the prior genuine callback remains intact.
Exact same-result/key/body replay returns original IDs without allocation/debit/
append; changed source/result/command conflicts. No carrier result/session,
assignment, submission, approval or receipt is manufactured.

Agent-read authorization/binding/context and initial scope have explicit new @2
encodings; old custody @1 bytes retain their own readers. Context pins original
source/result/claim, authority/profile capture and all complete native checkpoint
observations across waits. Pre-bootstrap native updated_at/last_heartbeat values
are retained independently; every other25 task fields must agree. Saved queued
rows compare all27 current native task fields exactly. K3a supplies no post-start
heartbeat/PID/result/terminal bridge, so unsupported mutation/drift refuses.
The existing native task row primitive is used only after complete prospective
move checks; ordinary task insertion retains its original guard.

The saved reader independently checks these original native start predicates:
The captured invocation has no uncertainty, including producer-settled uncertainty,
and its brief fingerprint equals SHA256 of the exact frozen source brief UTF8.
The frozen source has neither a completed_at nor a zombie_flagged_at marker.
Native journal shape and internally consistent pin digests alone do not prove
original validity. Running/count/identity checks use original frozen evidence;
later native journal phase or current authority is not substituted for that
admission. This correction adds no native lifecycle writer or execution proof.

The queued inventory and complete J schema reader (including data=False) now
forward one cumulative row/byte/step budget through the fixed release import.
Omitted/None import or queued budgets create one standalone allowance; a supplied
actual budget retains prior consumption and wrong types refuse categorically.
Release discovery, full typed publication/admission/journal reconstruction and
absence/unsupported-activation probes participate. Native rows gate their whole
payload before fetch; conservative UTF8/JSON/canonical/base64/hex intermediates
and traversal are reserved before nested pure parsing/reconstruction. Existing
stricter per-value/parser/ancestry limits and provenance checks remain. These
are cooperative finite-work limits, not hard process/SQL memory or time bounds.
Caller-owned transactions remain open; only standalone queued reads close their
own read snapshot. Inventories are reconstructed internally on that exact
connection/org/snapshot, without caller-trusted DTOs or global caches.

This is the partial SOURCE prerequisite under sole ROOT TASK-12299.
Current step17 manual observations distinguish assigned cumulative candidate
accounting/traversal dominance (component C413/T9601, combined C771/T16385)
and14 loader/resource safeguards from broader candidate8193 UNPROVED.
The native-only18-column overflow lower bound155649 visits is grammar-specific;
no global infeasibility, candidate-guard/full F5/F8/program/runtime PASS follows.
Historical native0/verifier2/42-INCOMPLETE and earlier ROOT10693 source waivers
remain history. They do not waive current allocated M1–M6/S1–S8 sensitivity,
forward restoration, GREEN/five fresh-process repetitions or independent
same-head executable QA. See docs/local-ci.md for exact command and exits.

The start_in_writer/support._storage pre-write composition now consumes the
internally reconstructed complete-schema mapping/import/queued inventory under
one cumulative allowance. Native source/result/ancestry and child reads share
that allowance; separate pre-admission checkpoints retain their own validation.
The retained source-data checker compares original release rows with that local
import history instead of reconstructing the history per row. Complete J native
discrimination is charged even without mappings/runs. No external inventory or
validation flag is accepted. Local reuse ends at the first write; post-write
complete validation reconstructs fresh state, and caller-owned transactions stay
open. Public schema readers retain their layout/None returns. Standalone
role/import/template readers retain their existing defaults and provenance.

Startup install_or_recover privately collects validated audit device/inode/last-rowid
and publishes only after positive COMMIT under the same Database RLock. Generic
complete readers and task classification collect privately and publish that exact
verified prefix only after all validation/classification and successful exit from
their own read snapshot. Classification retains the Database lock through publication,
including coding-absent recovery. Joined writer/savepoint/coherent-view readers do
not publish automatically; private collectors are output for explicit cooperating
owners, never validation proof. Failure, reconciliation, interruption, unsuccessful
snapshot exit, uncertain/failed COMMIT or detected identity/MAX regression cannot
publish. Independent installed/metadata readers retain source/naming/team/document
checks, including validate_data=False with coding present. The generic wrapper omits
only duplicate source/team checks proven by its own same-snapshot coding inventory;
absent/unproven coding retains them. Physical/FK, native/mutable and complete history
checks and the original cumulative allowance remain. Expected coding rows revalidate
below ordinary watermarks. This assumes cooperative insert-only history, not arbitrary
same-UID rewrites; no mutable inventory cache or expanded file-identity trust follows.

PR1165's prerequisite merged at fee7aa7222dbc80e16492258c15f8fa3214fabad,
with independent TASK12436 APPROVE and synthetic executable TASK12438 PASS at
reviewed aa9e64ad3a568b080571f86c6453d191aa6c706b (identical merge tree).
Those receipts retain their original-head attribution. TASK12440 supplied the
source composition; TASK12448 added65 reader/14 safeguard observations, but its
first qualified0b021bf5 invocation returned native0/verifier2 before every reader
case. That audit projection/type-arity failure remains setup INCOMPLETE, never
sensitivity RED. ROOT12299 step26 / TASK12457 aligns bounded complete-J audit
prefetch with all SIX original native columns and their unchanged strict storage
types on the SAME allowance. Earlier-layout/unscoped/unbounded scanning is unchanged.

The same maintained driver adds one pristine complete-J constructor assertion,
for66 reader observations plus the unchanged14 safeguards. Constructor exception,
layout/schema/native state and caller transaction are explicit observations before
dependent use; failed constructor assertions leave dependent rows unexecuted and
remain distinct from import/setup errors. Synthetic audit input uses its native
nonnull task scope, and aggregate typed-row expectations follow independently fixed
variant provenance. TASK12457 records attributable constructor RED/GREEN, allocated
local-only sensitivity/restoration and actual full/sibling repetition evidence;
its raw commands/heads/exits retain their own attribution. No inventory/resource,
allowance/type/schema/reference or native-owner radius is expanded.

Private-reader synthetic evidence does not establish actual
support._storage/start/native positives; those still require a separately
allocated genuine owner venue. Fresh FULL independent review and actual
same-final-head executable QA remain gates before live-manager guarded merge;
no native start or readiness override is supplied.
New settings/progression and literal @3/team_move
readers, repeated-binding inventory reuse and native controls remain root-owned
prerequisites. No complete admission/readiness closure is claimed. The earlier
SOURCE checkpoint's F5-R1c/F8-R1c UNEXECUTED venue statement is historical.
TASK12407's42+14 GREEN/five repetitions remain historical, as do its adverse
M6 partial27/native0/incomplete2 and S1/S3 cause-only results. Accepted step18
and TASK12419 repair the affected proof: independent queued fixture inputs,
actual emitted loader admission and the standalone reader's shared bounded scope.
M6/S1/S3/S4/S7/S8 require full-table intended RED and byte-exact restored GREEN;
S1/S3 observe invalid admission presence with zero bwrap attempts, retaining the
later native refusal. Unaffected variants retain original-head attribution.
PR1165 required exact-final-head42+14 GREEN/five repetitions and independent
review/executable QA; its actual outcomes remain in that PR/task case record.
Candidate8193 and broader native/runtime controls remain UNPROVED. Source review
and static checks are not behavioral or test-authoring acceptance; no old-root
waiver applies.
No schema DDL/reference changes, startup upgrade, old-byte rewrite, custody Q/U/P
issuance, R2 or daemon Git/object probe follows.
Future event families and partial/orphan/foreign/dual-owner graphs refuse. Valid
`coding_source_waiting`, `coding_carrier`, `coding_attempt` ownership cannot launch
through queue, draft/F5, ordinary chain/fanout/recovery or parent wake. Pause/move
rediscovery preserves this wait. Native cancellation of a selected queued graph
is refused before mutation, including ancestor cascade and after the DB-lock wait;
control/terminal writers remain later root-owned work. Initial selected move
projection contains effective actual roles, not unselected replacement candidates.

GET `/api/v1/orgs/{slug}/workflows/coding/runs/{instance_id}?session_id=CURRENT`
and `workflows coding run-show INSTANCE --org ORG --session-id CURRENT --json`
reuse current executable-manager loopback/session admission. Only the original
source-manager principal may select metadata, before private run decoding;
current mapping/effective-role authority is checked again. Closed
`workflow-coding-run@1` exposes exactly
`format,instance_id,release,mapping,effective_roles,cas_version,current_revision,target_revision,state,current_attempt,history,closeout,readiness`.
Queued history has its sole `run_started` metadata; no private native proof,
source callback, peer findings, repository content or execution grant is exposed.
Bearer fallback and browser/remote reachability remain absent. Raw and normalized
native GET candidates are denied independently using the existing one-decode
algorithm before credential lookup/forwarding. Served schema and classification
carry the same selected family; unrelated decisions keep legacy wire behavior.

This is disabled SOURCE delivery only. Independent full review, same-final-head
scoped SOURCE QA, normal commits/nonforce publication, local static all, literal
hosted Source Boundary Lint + Web Lint(Node24), and manager guarded merge remain.
Python units RETIRED; integration INCLUDING callback SKIPPED24342. SQL/parser/
behavior/security/race/mutation/red-green/repetition/native-build/E2E/protected/
executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430, never PASS.
Remaining K3 dispatch/native/control/supplements and K4–K6, genuine-agent revision/
fresh review/QA/CI/merge, two actual org defaults/override and backup-copy acceptance
remain on sole ROOT10693. No live migration/assignment/deploy/restart/enable/cutover.
<!-- /K3a result-owned queued bootstrap: TASK12003 / ROOT10693 step87 -->


<!-- Active coding contract: Founder THR139 seq640 / ROOT10693 step79 -->
## Active coding contract — agent-read subjects (seq640)

Founder THR139 seq640 supersedes the earlier seq559/562 coding content-serving
prerequisites and the historical K2b setup/mapping/source checkpoints below.
Those checkpoints and accepted artifacts/hashes remain historical evidence;
they do not require rebuilding their producers. Document contracts, native
permissions, F–J definitions/references and retained history remain in force.

Coding records repository identity, object format, immutable comparison base/head
and optional PR navigation. An eligible assigned agent reads code through its
existing authorized repository tools. Progression requires that agent's genuine
task/session/result, outcome and findings bound to the exact subject, with current
authority and independence. A descriptor/digest grants no access, inspection,
execution or merge authority. Unavailable access, a different subject or an
unassigned/inauthentic result refuses progression. Changed head requires fresh
independent review and QA; prior results and contributor/assignment/retry history
remain historical. Branch/PR navigation never identifies approval.

Reduced K2b on SAME draft PR1083 retains the fixed634-byte release import,
Founder predicate, original publication/admission/replay/current-authority and
complete template/history/cold/recovery/drain consumers. Import support now lives in
`_coding_import_support.CodingImportSupport`, extracted without a setup/adoption/
source prerequisite or expanded agent import authority. Draft setup issue/withdraw,
store adoption/mapping, source mint/refinement and their exclusive route/CLI/model/
contract/native entries are removed. Merged K1 stays dormant with its exact main bytes;
no daemon object read or content-resolution preflight is a coding prerequisite.
Rejected R2 private records, org fence and privacy projections are not implemented.
Keep bounded historical positive readers and malformed/orphan/foreign-data refusal;
reader retention neither restores a producer nor grants current access.

K2c separately supplies org role defaults, eligible repository scopes, per-work
implementer override, independence and pinned generation, not local-store custody.
K3 is the first atomic coding instance/carrier/development-task writer and ships
its own complete readers/recovery. K4 adds review/QA/revision with fresh exact-head
acceptance; K5 adds successful exact-head lint/static CI and guarded expected-head
merge/closeout; K6 adds operator entry/cutover. ROOT10693 retains genuine-agent
revision/re-review/QA/CI/merge, two-org defaults/override and backup-copy operational
acceptance. Coding remains disabled and runtime/security acceptance is missing.

No live migration, grant, provider allocation, deployment, restart, assignment,
cutover, cleanup or human trial follows. Python units are RETIRED; integration
INCLUDING callback is SKIPPED24342. SQL/parser/product/security/mutation/red-green/
repetition/native-build/E2E/executable proof is UNEXECUTED/FOUNDER-WAIVED139429/430,
never PASS. Independent FULL code review, same-final-head scoped SOURCE QA,
normal hooks/nonforce publication, lint/static checks and manager guarded merge
remain required. No new reader-only publication or replacement authority framework.
<!-- /Active coding contract: Founder THR139 seq640 / ROOT10693 step79 -->

<!-- K2c agent-read role defaults: TASK11987 / ROOT10693 step83 -->
## Agent-read role defaults and pinned selection (seq640)

K2c adds current executable-manager CLI POST/GET
`/api/v1/orgs/{slug}/workflows/coding/mappings?session_id=CURRENT` and
nonallocating POST `/api/v1/orgs/{slug}/workflows/coding/role-selections?session_id=CURRENT`.
These reuse existing native task-session/Pause/move/publication admission, with
no bearer fallback, browser or remote connector reachability. CLI forms are
`workflows coding mapping-publish --org ORG --session-id CURRENT --from-file ABS --json`,
`mapping-show --org ORG --session-id CURRENT --json`, and
`role-select --org ORG --session-id CURRENT --from-file ABS --json`.
Keep the original file/key on transport loss; no automatic retry.
The route streams the bounded body outside authority/team locks. The service
alone acquires authority, then teams, then DB/binding ownership for GET, publish
and selection. Native checkpoints use DB-before-binding order; DB is released
before waiting for teams. This agrees with authority-first configuration writers
and agent-update bootstrap/compensation. After each awaited acquisition the
serving org/session/team is rechecked; native invocation, executable manager and
current policy are rechecked at the coherent DB boundary before private decoding,
current use and writes. No non-reentrant authority reacquisition is needed.

Canonical bounded64KiB `workflow-coding-mapping-command@2` has exactly
`format,operation_key,expected_generation,mapping`. Mapping has exactly
`release,roles,eligible_replacements,repository_references,authority`.
Release remains the imported fixed coding v1/digest; four roles are implementer,
code_reviewer, qa_engineer and agent_manager, each `{principal,team}` with
permanent active agent IDs, four distinct principals, at most16 eligible
replacements per role and no replacement collision with another default role.
Agent manager is an actual executable registered agent manager, never Founder.
Every default and eligible replacement must belong to that initiating manager's
own authorized team, including the actual selected implementer override. A worker
in another team of the same org refuses even with an identical repository origin;
shared membership/repository access grants no cross-team action. Original saved
role records validate this radius against their authentic issuer, native manager
team and captured snapshot; current use validates it again under current policy.
Previously admitted out-of-radius role records now fail closed as invalid history,
without rewriting bytes or using today's roster to invent historical authority.
Valid same-team history and separate historical custody formats remain readable.
Each of1..8 repository references is exactly
`{repository_id,repo_key,origin_digest,object_format:sha1|sha256}`. Existing org/key/
configured-origin identity and repository permission must agree for every role
and eligible replacement. No local-store adoption, Git/object/content/provenance
probe, dedicated setup/source capability, R2 record or inspection Boolean exists.

Append-only `workflow-coding-agent-read-role-record@1` uses the unchanged TEN J
mapping columns, with explicit org, full original command/digest, genuine issuer,
native complete admission snapshot, mapping and original timestamp. It has no
custody/adoption fields and never reinterprets historical mapping-record@1.
One existing admission transaction writes mapping, five-column admission and
original native `coding_source_admission` capture; complete cold closure precedes
COMMIT. Full command replay in the same org/principal/key namespace precedes
fresh generation CAS and returns original IDs with current disposition, without
recapture or grant restoration. Changed bytes conflict. New POST201/replay200,
GET200; malformed422, missing404, revoked403, stale/conflict/unavailable409,
corrupt-history500. Closed `workflow-coding-mapping-receipt@2` has exactly
`format,id,generation,mapping_digest,mapping,replayed,current`; current is
`{state:active|revoked,reason:null|Token}`. GET refuses currently revoked use.
After every await and in the writer, native current session/owner/ancestry,
Pause/move, full published policy and role/repository eligibility are rechecked.

Canonical `workflow-coding-role-selection-command@1` is exactly
`{format,mapping:{id,generation,digest},implementer_override:null|{principal,team},subject}`.
Only implementer may override, from that immutable generation's eligible set;
all four actual selected principals stay independent and current. Defaults and
all pinned history remain unchanged by selection. Later default updates affect
future default GETs; an older exact pin is selectable while current authority
remains eligible. Unknown pin404, mismatched generation/digest409. Subject is
existing `workflow-code-change@1`: exact repository reference/object format/full
base+head are required; branch/PR navigation carries no approval identity.
The closed response `workflow-coding-role-selection@1` contains exactly
`format,mapping,roles,subject,overridden,coding_enabled:false`. It allocates no
task/result/instance/carrier/run/stage/effect or entitlement, and proves neither
object existence nor actual inspection. Current complete storage still refuses
ALL coding runs/attempts/events; K3 must compose genuine per-work contributor
history before its first allocation, never trust caller-provided history.

Saved/native/current/public/CLI/template/schema(data=False)/cold/reopen/recovery/
drain consumers validate complete mixed old/new mapping prefixes and all TEN
original typed columns, own full admission/publication/native capture and original
command/generation identity. Unknown/malformed/orphan/foreign/gapped/conflicting
families refuse without repair. Shared bounded prefetch/native row/raw-byte/step
budgets remain; no per-family reset, schema-object filter, startup upgrade, old-byte
rewrite or empty reset. Old binaries may refuse this new saved record family even
though J DDL is identical: use compatible code, with no downgrade-readability claim.
Historical setup/mapping/source/import readers remain; new role records cannot
serve as historical custody/source grants. F–J definitions/references, document/
human receipts, native security/lifecycle mechanisms and dormant K1 are preserved.

This is SOURCE delivery only. Independent FULL review, same-final-head scoped
SOURCE QA, normal hooks/nonforce publication, local static all, literal hosted
Source Boundary Lint + Web Lint(Node24) and manager guarded merge remain required.
Python units RETIRED; integration INCLUDING callback SKIPPED24342. All behavioral/
SQL/parser/security/mutation/red-green/repetition/native-build/E2E/protected/
executable/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430. Coding remains
disabled. ROOT10693 owns K3–K6, genuine-agent rejection/revision/fresh review/QA/
CI/merge, two actual org defaults/override and backup-copy operational acceptance.
No live migration/grant/assignment/deployment/restart/cutover/trial renewal follows.
<!-- /K2c agent-read role defaults: TASK11987 / ROOT10693 step83 -->




<!-- K2b source producer checkpoint: TASK11918 -->
**Historical nonallocating coding source producer — ROOT10693, Founder139562/559/329/595/606.**
Agent-only loopback POST `/workflows/coding/sources?session_id=CURRENT` and
GET `/workflows/coding/sources/{authorization_id}?session_id=CURRENT` use the
existing current manager transport, without bearer fallback or browser/remote
reachability. CLI `workflows coding source-mint --org ORG --session-id CURRENT
--from-file ABS --json` and `source-show AUTHORIZATION --org ORG --session-id
CURRENT --json` preserve one original file/key, with no automatic retry.
Canonical bounded64KiB `workflow-coding-source-command@1` is exactly
`{format,operation_key,parent_id,mapping:{id,generation,digest},action,work}`;
work exactly `{repository_id,store_owner,object_format,base,paths,commands}`.
Only import-release-coding, publish-coding-mapping and adopt-local-store source
actions mint here, within the current reusable U and all narrower parent edges.
Server derives genuine current task/principal/team/SID/manager_decision,
ordinary invocation/Pause/move/complete ancestry, callback absence and original
limits. Native owners and executor enforcement are composed unchanged.
The exact selected mapping/adopted store, source_rule/base_rule, executable
manager repository policy and action/path/argv intersections precede private
object work. Existing supervised adopted-store object machinery verifies the
fixed full base outside writers under one deadline; it returns no repository
content. Fresh native ALL27/ancestry/current policy checks repeat after awaits
and in the atomic writer. No timestamp exemption or counter refill applies.
A genuine immutable source P and its complete OWN five-column admission mint
atomically. Exactly one ORIGINAL source-phase capture retains all original27
source/ancestor/native session-start/invocation/counter/limit bytes; NULL result
and claim mean zero callback/carrier/task/run/stage allocation. Same-owner source
refinements obtain distinct own admissions while retaining the original
source_admission_id and capture. P/U/capture encodings and F–J DDL stay unchanged.
Private audit action coding_source_operation stores closed
`workflow-coding-source-operation@1 {format,org_slug,principal,authorization_id,
admission_id,session_id,command_bytes_base64,command_digest,created_at}` per mint.
This D1 operation metadata preserves each complete command/key/principal for
byte replay; it grants nothing and cannot replace an original capture.
Replay finds original bytes before fresh CAS/object work, returns original
identities/current disposition and never recaptures, reissues or restores grants.
Closed unsigned `workflow-coding-source-receipt@1` is exactly
`{format,authorization_id,admission_id,source_admission_id,operation_key,
request_digest,replayed,current:{state,reason}}`; current is active/inactive/revoked,
with a reason only for revoked. Changed same-key bytes conflict.
Mixed setup/import/mapping/P cold consumers share bounded native source/mapping
capture/operation inventory and the enclosing row/byte/step budget. Full positive
native-row equalities and unknown/orphan/duplicate refusal cover schema(data=False),
OrgState reopen, recovery and drain; ordinary nonmapping watermark behavior stays.
Source expected_head remains NULL/own-development-output@1: it grants no arbitrary
head disclosure, review, QA, CI or merge. K1.resolve remains unavailable because
no genuine exact-subject read entitlement is produced here. Allocating source
callback/C0, run C1/C2, native heartbeat and attempt/control/effect producers remain
K3 and later units, not fabricated subjects/results/grants. Agent release import
and source-to-action use remain separate from these mint metadata operations.
This is an incomplete SAME DRAFT PR1083 source checkpoint, never a delivered
coding engine, operational grant, deployment or merge. Independent FULL review,
same-final-head scoped SOURCE QA and literal lint/static gates remain required.
Runtime/security/rollback guarantees remain UNVERIFIED, engineering_manager-owned
before operational acceptance. Python units RETIRED; integration INCLUDING callback
SKIPPED24342; SQL/parser/product/timing/security/negative/mutation/red-green/repetition/
native/build/E2E/protected/executable-QA/test-authoring proof
UNEXECUTED/FOUNDER-WAIVED139429/430. No live setup/adoption/grant/assignment follows.
<!-- /K2b source producer checkpoint: TASK11918 -->


## Historical K2b mapping publication/adoption checkpoint — ROOT10693 step70

SAME incomplete OPEN DRAFT PR1083 now includes the explicit nonallocating
mapping writer and every own saved/native/current/cold/public/CLI consumer.
POST `/api/v1/orgs/{slug}/workflows/coding/mappings?session_id=CURRENT`
accepts canonical `workflow-coding-mapping-command@1` with only
`format,operation_key,expected_generation,mapping`; mapping has exactly
`release,roles,eligible_replacements,repository_selectors,authority`. Each of
at most eight selectors is exactly `repository_id,store_owner,repo_key,policy`,
with policy `adopted-local-store@1`. No paths, URLs, proof flags or server
capsules are accepted. GET on the same path requires the same genuine current
acting manager binding, selects the latest complete generation and rechecks
current setup/role/store eligibility. Both return the closed six-field
`workflow-coding-mapping-receipt@1 {format,id,generation,mapping_digest,mapping,replayed}`.
POST is201/new or200/exact replay, GET200; missing selection404, malformed422,
revoked manager/scope403 and unavailable/stale/conflicting admission409.
CLI `workflows coding mapping-publish --org ORG --session-id CURRENT
--from-file ABS --json` and `mapping-show --org ORG --session-id CURRENT --json`
use actual loopback session transport, without bearer fallback or automatic
retry. Preserve the original key/file after transport loss. Both routes remain
browser-excluded and native remote-denied on raw and normalized candidates
independently before credential lookup/forwarding.

Server selection derives the actual assigned executable team manager, task/SID,
unique native manager_decision session_start, real ordinary/v2 TaskInvocation,
Pause journal/generation, current tree/move admission and no own-SID callback.
Reusable M.setup roots authorize publication and each adoption separately for
its repository/action/store/format; neither roster membership nor a parsed
capsule grants permission. Fixed coding import and complete published role and
replacement eligibility are required; four contributors stay distinct. Empty
repository selectors require one unambiguous eligible setup; no arbitrary root
selection or empty grant becomes authority.

Configured primary stores are resolved server-side. A shared bounded no-follow
preflight checks custody, ordinary .git/config/object format/origin and object
inventory, refuses linked/indirect/symlink/alternate/promisor/shallow/replace/
graft/helper/unsupported stores, and executes no Git, hook, filter, credential,
fetch or clone. It derives server adoption-proof@1 and config/store identity.
After the await, repeat observation and compare before the SQLite writer, then
revalidate actual native authority/setup/current owner and generation in the
writer without filesystem/object work or awaits. Local adoption attests local
storage, not remote provenance or hostile same-UID/OS isolation.

One native admission transaction appends the exact ten J mapping columns,
complete canonical command/issuer/capture/M roots/adoptions/mapping bytes, and
its fixed original source-phase native capture. Full original task/ancestry,
unique session-start ID, actual invocation ID and admitted counters are retained;
result/claim/callback fields remain genuinely null. Final complete cold closure
precedes COMMIT; rollback preserves prior rows/ownership. Exact replay compares
org/real principal/key plus ALL original command bytes before fresh generation
CAS/adoption I/O, keeps original IDs and issues no work or renewed authority.
Historical validity does not establish today's permission. Complete positive
whole-row family matching covers mappings, original capture audit and all five
admission columns; orphan/foreign/malformed/unsupported families still refuse.
Central schema validation, also data=False, reopen/recovery and drain/downgrade
compose that same reader without recursive schema validation or ID exemptions.
Central shared-table scans first discriminate ordinary history independently of
coding-family accounting, including after mapping or queued enrollment. Expected
coding keys only locate freshly acquired rows; they never authorize acceptance.
Coding candidates use singleton rowid keyset pages with explicit overflow refusal.
Before their bounded Python fetch, every selected scalar/variable cell gates its
original SQLite storage class and each TEXT/BLOB UTF8 byte length (including multibyte/NUL suffixes),
and the whole row must fit the remaining shared raw-byte and candidate budget.
Original typeof and a separate refusal sentinel preserve genuine nullable NULL;
invalid/oversized rows never become coerced, truncated or partial success.
One call shares8192 fetched candidates,16384 traversal steps and16MiB accepted
raw bytes across setup, mapping, original-capture and central selected scans;
per-value1MiB and mapping/setup inventories1024 remain upper bounds. Bounds may
refuse earlier; they do not bound total SQLite/process/decoded-object memory.
Complete expected mapping/own-admission/source-audit rows are derived once from
validated records, indexed only for lookup, then compared in full with exact
native types. Original capture inventory and required setup histories are cached
only within this coherent call. No mapping row or supported audit is exempted
by ID or watermark. Unknown/orphan/malformed/duplicate evidence still refuses.
The SAME enclosing traversal allowance also covers selected coding metadata discrimination:
every structured node/member, duplicate pair and classified member string,
and malformed lexical prefix/member/whitespace/string/escape visits consume it.
Nested lists/objects and fallback after partial pairs-hook work never reset or
replace that allowance. Exhaustion refuses before a normal return, positive
admission or watermark advance. Ordinary shared rows do not spend this coding
allowance: decision prose mentioning workflow_start and noncoding org_config_write
sections remain ordinary. Coding settings scope is selected by config:coding OR
the decoded top-level section, preserving escaped/duplicate/malformed candidates
and the original1024 coding-publication limit. JSON-decoder/encoding internals,
total SQL/decoded-object/process memory and wall-clock work are not bounded by
this traversal or raw-byte accounting. Ordinary scans retain their historical
insert-only discriminator/watermark contract before and after mapping/start.
Expected coding rows always revalidate below that ordinary watermark. This source
correction is not behavioral/security verification.
Mapping/setup/import have no run/host tail; compatible J reader is always needed.
F–J SQL/reference bytes and all historical document meanings are unchanged.

Current K1 exact-subject disclosure still requires a genuine immutable subject
and selected entitled admission; the placeholder remains unavailable. SAME-K2b
remainder is source capability attenuation/current K1 subject selection and its
original source captures/current readers. K2c–K6 remain per delivery-map; K3 is
the FIRST run writer. No task/run/stage allocation, live setup/mapping/adoption,
migration/deploy/restart/cutover or retired trial execution is authorized.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; SQL/parser/
product/timing/security/negative/mutation/repetition/executable QA proof remains
UNEXECUTED/FOUNDER-WAIVED139429/430. Source assertions/lint are no behavioral,
security or test-authoring PASS. Fresh independent FULL review then same-head
scoped SOURCE QA and manager disposition remain required. Keep PR1083 draft,
unmerged and incomplete until the genuine SAME-K2b remainder is delivered.


## Historical K2b fixed coding release import checkpoint — ROOT10693 / Founder139562/559/329/606

Manager step68 extends SAME incomplete draft PR1083. This source checkpoint adds
explicit fixed release import and its own historical/current/public/cold readers;
setup-only descriptions below retain their original checkpoint meaning. K2b
remains DRAFT/unmerged until mapping/adoption/current K1 and source attenuation
with actual nonallocating task/SID/invocation/Pause/ancestor capture producers and
all their own readers are complete. First coding-run writer is K3, not this unit.

POST `/api/v1/orgs/{slug}/workflows/release-templates/coding/1/import` uses the
existing Founder human predicate and current native authority admission. It takes
only canonical UTF8 `workflow-coding-release-import-command@1` with exactly
`format,operation_key,expected_current_version`; the selector is INTEGER 0..1 (only0 can create the fixed version1) and
the stable key is 1..128 token characters. The full command is at most1024 bytes.
No query/body actor, task, SID, namespace, definition, gate or pin selector is
accepted. The server selects the exact634-byte packaged coding.v1 resource,
SHA256 `6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`,
namespace `release/happyranch`, template `coding`, version1 and pins
`workflow-coding-compiler@1`, `workflow-coding-validator@1`, `release-input@1`.
Human import creates no task/SID/result and does not require an invented machine
grant. Manager import remains unavailable until genuine source attenuation and
nonallocating admission producers ship on SAME K2b; identity/setup alone is not
machine authority. Existing org/team publication grammar remains unchanged.

CLI `happyranch workflows coding release-import --org ORG --from-file ABS --json`
sends the complete original file once with existing human transport; keep its
original key/file after transport loss. `release-import-show --org ORG --json`
uses GET on the SAME path. Both return a closed
`workflow-coding-release-import-receipt@1` with exact identity/version/admission
IDs, operation_key/request_digest/replayed, current `{state,reason}`, and the
unchanged disabled release projection. POST is201 for initial import,200 for
exact replay; GET is200 and never writes. Current state is admitted/unavailable,
not coding readiness. Entitlement precedes private decode; absent import is404,
invalid command/query422, stale admission/duplicate import/conflict409, corrupt
history/resource500, missing resource503. Both import methods are CLI-only,
browser-excluded and native remote-denied for raw and normalized paths
independently, including prefixes, one decode, literal query split and one final
slash. The original catalog GET remains write-free and enabled:false.

Resource I/O/reference construction/discovery occur outside writer locks; after
waits the real Founder and current native capture are revalidated. One native
admission transaction inserts existing draft/version/identity/version-link/
publish-operation/admission rows and validates complete cold closure before
COMMIT. `author_principal`/`published_by` retain equal typed canonical import
provenance `{format:workflow-coding-release-import-provenance@1,org_slug,publisher,
command_bytes_base64,command_digest,admission:{record,capture},created_at}`;
publisher is the actual Founder provenance, never a synthetic task principal.
The original command and native capture bind every row, exact resource hash/pins,
all five native admission columns and complete original publication/profile
journal. Ordinary document provenance is unchanged. Exact replay precedes fresh
CAS, returns original IDs, changes no authority pointer, and separately reports
current policy loss. A different key cannot create version2 or another import;
changed bytes under the same key conflict. Missing package I/O can hold a fresh
import while exact saved replay remains readable. Any partial/foreign/malformed/
extra/orphan/incorrectly typed relation refuses; failures roll back all new rows.

Central J validation, including validate_data=False, source-data validation,
startup/reopen, recovery pre-legacy discrimination and drain/downgrade all consume
complete immutable import closure; no ID exemption or compiler-pin-only admission
exists. Saved history is independent of today's permission. Ordinary template
listing includes the shared org-local imported release and exposes only actual
publisher provenance, never private capture bytes. Exact release store get and
low-level version readers validate the complete import and J before returning;
the public selected receipt is the dedicated GET/CLI above. Import cannot bind
an activation or document binding: unsupported code execution continues to refuse.
F–J SQL/independent references, create-org I, generic Database/runtime-audit,
K0 disabled readiness and unavailable K1 current resolution remain unchanged.
No startup/catalog import, mapping/P/run/attempt/event/receipt writer or live
operation is added. No schema, authentication, credential, shared transport,
permission, callback, hook or dependency mechanism changes follow.

Python units RETIRED; integration INCLUDING callback SKIPPED24342. SQL/parser/
behavior/security/negative/mutation/repetition/executable-QA proof is UNEXECUTED /
FOUNDER-WAIVED139429/430. Native assertions are retained source, not executed
coverage. Lint/static checks and inert OpenAPI inspection do not prove behavior.
Fresh FULL independent final-head review then same-head scoped SOURCE QA return
to SAME sole ROOT10693. No maker approval/QA/merge or complete K2b acceptance.

## Historical K2b first setup producer checkpoint — ROOT10693 / Founder139562

Manager step65 and THR139seq595/596 select **one K2b draft PR**, continuing
merged1082. Each writer ships its own complete saved/current/native/cold readers;
future run/stage readers are required with their corresponding writers. Earlier
K2a1/K2a2 sections below are historical checkpoint evidence, including their
then-current empty-only/no-producer and all-future-readers-first descriptions.
They do not impose another reader-only publication or change accepted encoding.

This checkpoint supplies Founder setup ISSUE/WITHDRAW, selected GET and exact
replay. POST `/api/v1/orgs/{slug}/workflows/coding/capabilities` accepts only
canonical UTF8 `workflow-coding-capability-setup-command@1` with exactly
`format,operation_key,setup_key,expected_authorization_revision,mode,scope`.
Issue scope fixes repository, actual published manager/team and configured store
owners, finite actions/path/argv maxima, source_rule and base_rule. Empty path
or command scope grants none. Withdrawal requires null scope and genuine prior
issue; its new immutable issuance is an inactive tombstone. No body identity,
agent session, bearer forwarding, query parameters or automatic retry is allowed.
Existing `_require_human` derives the Founder principal on the server.

CLI `happyranch workflows coding capability-set --org ORG --from-file ABS --json`
uses existing human transport and preserves the full original canonical command.
GET `/api/v1/orgs/{slug}/workflows/coding/capabilities/{setup_key}` returns only
the selected closed `workflow-coding-capability-setup-receipt@1`: original
IDs/revision/key/digest/replayed plus current active/inactive/revoked disposition.
Both endpoints are CLI-only/browser-excluded and native remote-denied; denial
checks raw and normalized candidates independently before forwarding. No shared
normalizer, credential or permission semantics change.

Setup records require compatible complete J. Every native admission's five
columns, original Q/issuance bytes, full snapshot/profile mirror/publication
journal, contiguous revision prefix, unique setup/operation/admission identities
and exact latest pointer are checked. One native admission transaction inserts
admission/revision and CAS-updates the pointer; failed validation rolls all back.
Discovery and independent whole-native schema reference construction precede
transactions; final admission uses captured data with current authority checks.
No file/object I/O or await occurs in the writer. Replay compares full original
bytes before fresh CAS, changes no pointer/revision, and reports original IDs
with current inactive/revoked state after withdrawal/replacement/policy loss.
Cold-valid history never establishes current authority or action permission.

Central J validation (also validate_data=False), reopen, recovery's pre-legacy
boundary, drain and downgrade recognize exactly complete setup closure. All
mapping/source/P/run/attempt/event/receipt coding families remain categorically
unsupported; malformed/duplicate/orphan/foreign values refuse. F–I/J DDL and
independent references stay unchanged; create-org remains I, startup never
upgrades, even empty J requires compatible code. Generic Database/runtime-audit
remain workflow-free. K0 stays disabled and K1 current resolution unavailable.

**K2b remains incomplete and DRAFT/unmerged.** SAME-PR mandatory remainder:
explicit fixed release import; mapping publication/local-store adoption/current
K1 resolution; source attenuation and genuine nonallocating task/SID/invocation/
Pause/ancestor capture producers, each with complete own current/native/cold
consumers. K2c owns role defaults/overrides/fresh J; first run writer is K3;
K4/K5 own revision/QA/CI/merge closure, K6 operator entry/cutover. No live setup,
grant/adoption, SQL fixture, deployment/restart/migration/assignment, provider/
browser/native build/ad hoc E2E or retired trial execution follows source work.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; SQL/parser/
security/negative/mutation/repetition/behavior/executable QA UNEXECUTED/
FOUNDER-WAIVED139429/430. Lint/static evidence supplies no behavioral PASS.
Fresh FULL independent review and same-final-head scoped SOURCE QA return to
SAME sole ROOT10693. No setup-only merge or complete K2b acceptance is claimed.

**Disabled coding release catalog K0 — Founder THR139seq562/563, ROOT TASK10693.**
GET `/api/v1/orgs/{slug}/workflows/release-templates/coding/1` authenticates the
existing bearer and resolves an existing org before reading the constant package
resource. CLI `happyranch workflows release-template coding --version 1 --org ORG
--json` calls that GET and validates the same closed receipt. Exactly634 canonical
UTF8 bytes pin SHA256 `6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`.
The matched `workflow-release-template@1` receipt exposes exact bytes/base64,
identity/version/digest and compiler/validator/source pins; readiness is ALWAYS
`{enabled:false,reason:"release_catalog_only"}`. Missing/unsupported resource
returns503 `resource_unavailable`; corrupt/noncanonical data returns500
`resource_corrupt`, without private paths. Unknown kind/version is404.
One wheel force-include pair feeds the unchanged shared frozen inventory; actual
installed/frozen loading remains unverified. Import/mount/GET never seeds,
imports templates, reads mutable readiness, changes workflow data or allocates
work. The ordinary org GET is classified included for unchanged native handling;
K0's explicit radius ships no TS/provider/browser initiation or stage prompts.
No J migration, setup/mapping/adoption capability, activation, execution, CI/merge
engine or live operation follows. Later ordered coding is separately scoped
under Founder562; historical document pins and exclusions remain intact.
V1 later merge requires exact independently reviewed head plus successful required
lint/static CI on that head, scoped QA/hooks and guarded manager merge; optional
target-base conditions cannot block v1. Artifact comparison base remains fixed.
See [the current K0 source contract](../../superpowers/specs/2026-10-14-ordered-coding-workflow.md).
Python units RETIRED, integration/callback SKIPPED24342; behavioral/protected proof
UNEXECUTED/FOUNDER-WAIVED139429/430. Source review is not behavioral acceptance.


**Bounded repository artifact source K1 — Founder THR139seq562/329, ROOT TASK10693.**
`repository_artifacts.py` supplies closed canonical saved binding/adoption/presentation
readers; the private `repository_artifact_objects.py` supplies bounded no-follow
adopted-primary-store copies, sanitized typed Git reads, recomputed object hashes
and complete raw base/head tree manifests. Allocation failures are category-only
storage_unavailable with no launch or acquired directory. Every refusal retains
the parent's acquired private-directory identity for owner remediation; process
quiescence and omitted child metadata do not prove cleanup. Worker cleanup stays
alarm-bounded; the parent performs no recursive cleanup and accepts success only
after witnessing directory absence. Unknown launch/nested process/reap ownership
remains internal. Retained directory identity is not a claim of present storage.
Saved validation proves historical integrity only. OrgState mounts `WorkflowRepositoryArtifactService`; its current
resolve operation categorically refuses before decode or object I/O because K2b's
authenticated adoption/current authority producer is absent. No constructor flag,
caller Boolean, record/digest or injected callback authorizes content access.
K0 readiness remains false and code activation still refuses before allocation.
Current document formats/producers, routes/CLI/DTOs/schema/receipt/recovery and
native permissions are unchanged. Later coding writers must ship complete selected
read/stage/prelaunch/receipt/closeout consumers with before-I/O admission and
post-await authority revalidation. No live adoption/setup, execution or coding
readiness is delivered. Python units RETIRED; integration/callback SKIPPED24342;
behavior/security/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Source-only
review/static checks cannot establish reader-security or behavioral acceptance.
See [the current K0/K1 contract](../../superpowers/specs/2026-10-14-ordered-coding-workflow.md).

**Empty-only coding schema K2a1 — Founder THR139seq562/329, ROOT TASK10693.**

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

Workflow whole-reference selection retains F–J and adds workflow-only fifth
history `v2-chronological-20260530` (THR320seq150, accepted ROOT12848 composite
TASK12862/addendum, independent DESIGN-ONLY PASS12866). Its fixed whole seed is
56585 UTF8 bytes/SHA256
`9ac9a2034d63ecc502d4038b6d50f811692b8062c921c3d04115567f61ecaad7`:
195 objects independently constructed through complete historical Database
constructors bda5d062→1be72fe7 and the authentic thread sweep. Original S2 marker
and `sqlite_sequence(session_token_usage,0)` remain intact; observed/live schema
is comparison only, never constructor input. Both old preimages remain literal.

Legacy authority still admits ONLY fresh/v0/v2/v2-organic, rejecting the fifth
name before shared cache lookup even after workflow cache fill. Generic workflow reference consumers retain their whole-reference helper;
coding import uses scoped storage validation. Cache identity includes
`(layout,history,naming,Pause,team_move,coding_extension,owner_indexed)`; ANY unavailable reference refuses
even if another digest matches. All nonNULL raw sqlite_master SQL ordered by
name participates, without normalization or object filtering. Authority-v2 stays
observed-only; generic Database/runtime-audit remain workflow-free.

Every reference first constructs the complete unindexed final layout, including
coding where requested, then optionally adds ALL canonical owner indexes on that
disposable reference and validates the final composition. Index counts without/with
coding are F31/32, E34/35, G36/37, H37/38; I document-only has 39, I+coding is J
with 40, and direct J has 40. Both wrappers default `owner_indexed=False` and reject
non-Booleans before cache lookup. Live variant selection follows complete validation
inside the same coherent read view; it never installs indexes on the live DB.

Only fifth-history J uses whole seed → current Database → unchanged complete-I
initializer with `owner_indexed=False` → shipped canonical coding DDL in
workflow_schema_transaction → require full validator J → optional complete J
owner indexes and full J validation → unchanged Pause/team-move/naming order → raw digest.
Exceptions/unexpected layout remain unavailable. Other fifth-history layouts
retain their F/E/G/H/I paths; old families keep their construction and digests.
The strict fresh-J initializer still requires empty native operational storage
and valid remote markers; it correctly refuses this retained sequence row.
Neither that guard nor any real migration changes; calling the real coding
migration here would recurse through reference validation. Actual create-org
still explicitly initializes I. Normal OrgState.load subsequently adds coding
beside the actual document foundation; I plus coding is J. Enable performs no
document upgrade. Empty J uses the existing I draft/submission/handoff/
revision and document drain readers. Any J, even empty, blocks pre-J downgrade;
retain compatible J code or a separately approved verified pre-J backup. No
stripping, automatic migration or old-reader downgrade claim follows.

Historical offline document/coding transitions remain separately owned,
with target inspection; normal coding setup no longer requires that chain; submission/coding check3 differ from
exact handoff/revision migration-required1, and refused1 stops. The first three
writers remove only --check; coding requires --confirm-cooperative-offline.
Exact import absence is HTTP404 coding_release_import_not_found/CLI1, never
empty success or clearance of uncertain original file/key/body/effects/new-key
retry. Founder save remains Admission Disabled; operational acceptance stays
NOT RUN/open. See the ordered-coding-workflow spec's historical compatibility
operator guidance for the host/offline/backup/owner prerequisites.

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

Writes require positive current stopped state and complete current writer,
preparation and process exclusion throughout the affected registered-runtime,
normal-org/shared-daemon/store/source/environment and cooperative-other-host
window, including publications, profiles, moves, recovery and competing restarts.
Establish human control before constructor effects or a preceding source update;
repeat supported scope/process/lifecycle/identity observations before and after
effects and keep the original SQLite reservations. One census, PID absence or a
human yes alone is insufficient; unavailable present exclusion still refuses.
Historical pending jobs on ended owners, nonquiescent receipts, failed old
preparations/started audits and retained relocation storage alone require no
closure certification. Preserve their labels/ownership/keys/bytes; never settle,
relabel, clear or replay them. Current tasks, automatically consumable pending
work, source-result processing and current publication/profile/Pause/move/recovery
owners still hold. Complete data/history validation remains separate. This is
cooperative exclusion, not a cross-host lock, hostile same-user or hard real-time
guarantee. The original supported stop must succeed if attempted; independently
selected already-stopped entry skips stop without reconstructing old shutdown.
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

## Retained native source identities — K2a2b4

Sole ROOT10693 step60 / Founder THR139seq562/559/329 accepts TASK11484
source appendix SHA2564ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1
and cases SHA256f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae
after independent DESIGN11485. Unmounted coding_source_history now adds
read_source_native_records(conn, expected_org_slug, authorization_id), with
exactly five immutable fields: capability_history, source_task_record,
ancestor_task_records, session_start_audit_record, same_session_result_records.
Existing capability/setup APIs and mandatory-value validations stay unchanged.

Select genuine source-level P and its complete native P/U/own-admission/historical
publication closure with one shared call budget/cache. All27 task/18 result/6
audit fields preserve exact native storage classes, genuine NULLs, empty free
text, original UTF8/JSON bytes/timestamps and finite native REAL cost. Current
source and complete parent-to-NULL ancestry (20 rows INCLUDING source) are
TODAY'S coherent records, never original source admission or current permission.
Ancestry follows only parent_task_id; missing/cyclic/foreign-team rows refuse.
Original source SID selects one authentic manager_decision session_start with
the exact native owner payload grammar and the complete bounded same-SID result
inventory. A selected genuine INTEGER callback matches the full closed original
workflow_start command and its source tuple/INITIAL source work radius. A null selected result
never proves original absence or excludes later callbacks. No old active tracker
binding, current assignment equality, claim consumption or source grant follows.

Caller first admits complete native/J structure and holds one coherent raw-value
connection/transaction/lock. Fixed parameterized SELECTs only; no transaction
control/await/DTO/default counter/converter. Original typeof and byte lengths gate
before fetch, including multibyte/NUL suffixes. Every new native row gates ALL
variable bytes against remaining budget before payload fetch; singleton keyset
pages renew that gate. A bounded uniqueness probe prevents task LIMIT1 masking.
All fetched candidates, including rejected/overflow rows, charge one8192-row/
16384-step/16MiB raw-byte budget; per-value1MiB, P64/setup1024/source20.
Native nullable/empty/REAL accounting is separate from mandatory setup/admission
validation. Limits do not claim total SQLite/process/decoded-copy memory bounds.
Failures are category-only coding_source_history_invalid with no partial return.

Original callback C0 and first claim C1 require independently admitted captures;
only their exact25 named load-bearing columns may agree with separately retained
timestamp observations at that initial boundary. ALL27 C1=C2 equality is mandatory
after preflight; intervening heartbeat refuses without any partial coding write,
debit or publication. Full27 attributed replay/version-aware heartbeat handoff
remain mandatory after bootstrap. These capture/writer/replay obligations remain
UNIMPLEMENTED here. Fixed callback/source captures plus independent historical
consumer, complete J/native/current-policy/invocation/Pause/move/host/recovery/
cutover closure must precede EVERY producer. No capture/native writer/mount,
authority inference or live operation is supplied. Existing F–J/refusal/create-org
I/document history, disabled K0 and unavailable K1 remain. FIRST fresh FULL
reviewer then same-final-head scoped SOURCE QA then SAME ROOT10693; static-only
local all/hosted lint, normal hooks/nonforce push and guarded manager merge.
Python units RETIRED, integration INCLUDING callback SKIPPED24342; all SQL/parser/
product/native/security/negative/mutation/repetition/executable proof remains
UNEXECUTED/FOUNDER-WAIVED139429/430. Root retains K2a2–K6/operational acceptance.

Native result `learnings` is nullable free TEXT, inserted unchanged by its owner:
NULL, empty, prose and UTF8 bytes remain exact in EVERY same-SID result. Only
owner-serialized JSON columns undergo JSON validation; all native storage/UTF8/
prefetch per-value and shared-byte gates remain. The selected allocating callback's
full original work repository/object-format/base/paths matches the INITIAL source
P exactly, taken from the first node of the already validated complete native
source-to-selected lineage. Same-issuer source refinements may narrow paths and
retain that original callback; the selected P and all parent attenuation checks
remain intact. A callback matching only a narrower child, conflicting identity,
missing native ancestor or widening refuses; no caller-picked ancestor, broad
subset substitute, extra query, historical rewrite or current grant follows.

### Original source-capture history — K2a2b5

ROOT10693 step63 / Founder139562/559/329 selects accepted TASK11484 appendix
4ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1 and cases
f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae after DESIGN11485.
Unmounted `coding_source_admission_history.read_source_admission_history` consumes
the fixed `coding_source_admission` / `workflow-coding-source-admission@1` audits.
It returns immutable original source/callback captures, explicit INITIAL native P
and native identity links, and allocating full-decision fingerprint. Selection by
authorization_id keeps the complete selected attenuation but never substitutes a
refinement's own mint admission or a mapping capture for the original source P.
The existing five-field native identity API and its free-text learnings/INITIAL
work-radius behavior remain intact through private shared-budget composition.

Closed phase/owner/claim/budget and lossless typed27-task/18-result rows retain
NULL/empty UTF8 bytes, strict signed INTEGER and finite binary64 bits, complete
ordered original parent-to-NULL ancestry, original command and raw native audit
JSON bytes (which need not be canonical). Native immutable task identity/parent,
full result/decision and genuine unique manager_decision session-start links are
checked separately from today's mutable assignment/SID/counters. Allocating C0
and C1 compare exactly25 named load-bearing fields in every source/ancestor row;
both original updated_at/last_heartbeat observations remain independently valid
and retained. Nonallocating captured NULL means absence at that admitted boundary,
never absence of later callbacks or allocating permission. Source claim/invocation
ID/frozen limits are retained facts; actual invocation/Pause and policy proof is
still separate. Zero max_revise_rounds keeps its native meaning.

One coherent caller-owned raw SQLite transaction/lock; SELECT only. Complete
bounded fixed-action audit inventory exposes duplicate/conflicting/foreign owners
without JSON-selection predicates or latest-row fallback. Native typeof and each
row's remaining row/byte ceilings gate payloads BEFORE fetch. Shared8192 fetched
candidates/16384 traversal steps/16MiB byte budget includes conservative decoded
byte copies, with1MiB per value/whole raw audit, source20/P64/setup1024. These are
not total SQLite/process/JSON-object memory limits. Missing required captures
raise coding_source_capture_unavailable; invalid evidence raises category-only
coding_source_history_invalid, without private details or partial return.

This delivers the historical capture consumer only. Callback/source capture
WRITERS, direct mapping-history capture selection, C1=C2 ALL27 writer equality,
full J run_started/source-baseline/replay/native meaningful-writer and heartbeat
bridges, actual invocation/Pause/move/host/terminal/outer cleanup, historical and
current policy/source_rule/base/permission and recovery/cutover readers remain
mandatory BEFORE EVERY producer. No J validator, complete source authorization,
valid-run Boolean, native/public/CLI/TS mount or producer permission is supplied.
F–J/populated/reserved refusal including validate_data=False, create-org I,
document histories, disabled K0/unavailable K1 and protected owners stay unchanged.
FIRST independent FULL review, then same-final-head scoped SOURCE QA, then SAME
ROOT10693; static local all/Source Boundary Lint/Web Lint(Node24), normal hooks,
nonforce push and manager guarded merge remain. Python units RETIRED; integration
INCLUDING callback SKIPPED24342; SQL/parser/product/security/protected/executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Root retains K2a2–K6, real-agent/two-org/
override/backup-copy operational acceptance. No live action or trial renewal.

<!-- TASK11932 current-ancestor correction and owner support boundary -->
**Historical TASK11932 checkpoint.** Seq640 removes this producer/worker path;
its unresolved finding and rejected R2 proposal stay history, not a prerequisite
or a runtime cleanup/security PASS. The active contract above controls.

Every selected source ancestor now revalidates its OWN saved operation and
original mapping against current admission before object work and again in the
writer. One bounded iterative traversal shares the native owner and mapping
selection; historical containment never substitutes for current admission.
P1 minted under M1 cannot authorize P2 under newer M2 while P1 is revoked; deeper
stale ancestors also refuse. Original GET/replay keeps original identities and
returns revoked metadata without recapture; an eligible initial source under U
is independent of that revoked lineage.
TASK11927's preflight ownership finding remains OPEN: the current request does
not durably retain worker/process/private-storage obligations through refusal,
cancellation or org close. A concrete private ownership support delta is returned
to ROOT10693 before dependent persistence/lifecycle edits. No cleanup, restart,
security or cancellation guarantee follows from this source correction or lint.
SAME PR1083 remains incomplete OPEN DRAFT and unmerged; independent FULL review
and same-final-head scoped SOURCE QA are still required. Python units RETIRED;
integration INCLUDING callback SKIPPED24342; behavioral/security/cleanup proof
UNEXECUTED/FOUNDER-WAIVED139429/430, never PASS. No live operation follows.
<!-- /TASK11932 current-ancestor correction and owner support boundary -->

<!-- K3b native development: TASK12023 / ROOT10693 step91 -->
## Disabled first-development native ownership (K3b)

The production CodingDispatcher is mounted beside the coding start owner. Actual
owned routing, queue admission and TaskInvocation use the explicit `coding`
owner; source-wait and carrier remain structural. Both global start/callback
readiness and actual coding queue/dispatch admission remain unconditionally
unavailable before SID/host effects. No caller/config readiness Boolean exists.
This source slice does not enable the coding engine or allocate a next stage.

The first-development journal pairs full native rows with session_bound,
launch_reserved, actual handle/PID launch_observed/host_reconciled, own INTEGER
callback_recorded, terminal_recorded and late cleanup_settled observations.
Callback alone cannot complete or advance. Real terminal success still waits
for tracker/reconciliation/task-tail/root-worktree-batch/settle_live closure.
Original captures remain unchanged; unknown teardown retains the same owner.
The fixed native coding_task_heartbeat audit overlays exactly last_heartbeat and
updated_at under the actual queue worker lifetime. Unattributed full-row drift
and unsupported stage/control/cancel/retry/successor mutations refuse.
Complete mixed saved/native/J/schema(data=False)/cold/reopen/recovery/drain
consumers use the same closed first-development reader and full27 task equality.
New native `coding` journal values require compatible code, including on empty J.
F–J DDL/reference bytes and ordinary native owner/permission/host meanings stay.

The consumed development supplement pins original brief/repository/object format/
fixed base/paths and actual run/attempt/task/role/generation. The agent uses its
existing authorized tools; references grant no access or inspection assertion.
Head remains absent until genuine output. The final callback belongs to that
actual task/SID; no unavailable staging/advancement command is advertised.

Independent FULL code review then same-final-head scoped SOURCE QA remain
required. Local all and hosted Source Boundary Lint/Web Lint(Node24) are static
only. Python units RETIRED; integration INCLUDING callback SKIPPED24342. All
behavior/SQL/parser/security/race/rollback/negative/mutation/red-green/repetition/
native-build/E2E/protected/executable/test-authoring proof is UNEXECUTED /
FOUNDER-WAIVED139429/430. K3 control/recovery and K4–K6 plus actual agent/two-org/
override/backup-copy acceptance stay ROOT10693-owned. No live operation follows.
<!-- /K3b native development: TASK12023 / ROOT10693 step91 -->

<!-- K3b four-finding correction: TASK12029 / ROOT10693 step92 -->
The K3b correction separates fresh launch from the original admitted owner's
callback. Once enabled by the still-missing whole-engine prerequisites, a genuine
running owner may submit its first full INTEGER callback after root Pause;
cancellation, foreign identity, stale ownership or revoked current authority
still refuse. Launch hold/capacity/session-list checks are not callback admission.
Exact historical callback replay is read-only, including while readiness is false.
Fresh callbacks and all coding launch entrypoints remain unconditionally disabled.

Bootstrap run_started stays literal event@1 and retains its original authority A.
Native lifecycle event@2/internal-command@2 adds a closed native-admission@1
observation at session_bound and callback_recorded: the actual full authority B
capture/publication, original native invocation/Pause, bounded causal task/move
closure, effective bindings, original limits and launch-only capacity facts.
Independent saved/native/current/cold/schema(data=False)/recovery/drain readers
reconstruct source-time predicates; today's roster or the unchanged bootstrap
capture cannot substitute for original dispatch or callback admission. Unsupported
old native events/heartbeats lacking these proofs refuse without rewriting bytes.

Before the first coding heartbeat, the actual queue worker prepares its real
TaskInvocation. Heartbeat@2 carries that native entry's invocation/claim and native
Pause admission generation; current task SID may still be null. The same identity
survives real SID binding and worker retirement. No synthetic claim is replaced;
ordinary duplicate delivery retains native ownership and teardown semantics.

Native cleanup consumes the actual committed close disposition and final move
release observation. Every development cleanup independently joins original
invocation/claim/task/agent/SID/owner/fingerprint and sticky uncertainty. Null,
foreign, unknown, missing close, failed save/release or retained unknown journals
cannot certify safe settlement. Positive removal requires owned close evidence;
missing-owner gaps retain the original owner and uncertainty. Ordinary close,
deferred ownership and exception ordering remain native; no host protocol or
persistence mechanism is added. F–J/Pause DDL and references remain unchanged.

These are disabled source corrections. Runtime case results remain MISSING;
Python units RETIRED, integration INCLUDING callback SKIPPED24342. Behavioral,
security/race/rollback/negative/mutation/red-green/repetition/native-build/E2E/
protected/executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
ROOT10693; K3 controls/recovery/K4–K6 and operational acceptance remain root-owned.
<!-- /K3b four-finding correction: TASK12029 / ROOT10693 step92 -->


<!-- K3b native service attribution: TASK12034 / ROOT10693 step93 -->
Native lifecycle event@2 service attribution is independently fixed to
`service:coding-dispatcher:` plus the authenticated org. The saved reader requires
both the event column and operation principal to equal that value and derives
the deterministic event identity from it. Recomputed keys/digests/pointers cannot
admit a foreign service, agent principal or another org's dispatcher. Genuine
worker/source evidence.origin and original native claim/admission/callback/cleanup
proofs remain separate and independently checked. Valid dispatcher history keeps
its original meaning; unsupported attribution refuses without rewriting history.
The common saved/native/J/schema(data=False)/cold/reopen/recovery/drain readers
apply this same check. All coding readiness remains false before SID/host effects.
Runtime observations MISSING; Python units RETIRED; integration INCLUDING callback
SKIPPED24342. Behavioral/security/negative/mutation/red-green/repetition/native-build/
E2E/protected/executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
ROOT10693; K3 controls/recovery/K4–K6 and operational acceptance remain root-owned.
<!-- /K3b native service attribution: TASK12034 / ROOT10693 step93 -->

<!-- TASK12488 / ROOT12299 first ordered edge -->
## Disabled development to independent code review source

ROOT12299 step28 / Founder THR320seq4/18/27 releases only the first
revision1 development -> code_review edge and the selected reviewer's native
lifetime. Readiness and require_ready remain unconditionally disabled. No
settings/cross-team binding@3, QA/final-manager allocation, merge effect,
activation or runtime acceptance is supplied. Earlier one-development checkpoint
statements describe their original source; this additive contract controls the
new explicitly versioned records without rewriting old records or receipts.

The closed workflow-coding-stage-output@1 has exactly format, instance_id,
attempt_id, revision, stage, subject, findings and outcome. subject is the real
workflow-code-change@1 descriptor with kind=code-change, repository:{id},
object_format, full lowercase head, comparison:{base}, navigation:{branch,pr_url}.
Repository/object format/base are fixed by admission; initial start has no head.
Findings are nonblank and at most16384 UTF8 bytes. The callback summary remains
a canonical UTF8 JSON STRING (sorted keys, compact separators, no duplicate or
unknown keys), never a new parameter/object. Development uses outcome=produced,
completed status and null verdict; code_review uses genuine APPROVE,
REQUEST_CHANGES or BLOCK with an exactly equal verdict. Failed/blocked/unusable
outputs retain original native evidence and cause, without an invented receipt.

Callback persistence does not advance. The complete reader reselects the actual
INTEGER result/task/SID/principal/attempt and positive successful real host and
tracker/task-tail/worktree/settle_live/close/move-release settlement. Unknown or
retained original owners hold. A separate current-authority/Pause/move/capacity
transaction accepts the exact subject, records its actual contributor and queues
one distinct pinned reviewer, using native uncommitted task insertion. The
business writer performs no await, file/remote I/O, enqueue or commit. Full target
closure precedes commit; committed notification and cold rediscovery follow.
Same original-result/CAS replay reuses the allocation. No active_chain alias or
self-committing generic delegation helper is used.

New edge event@3/edge-command@1/edge-admission@1 and review-scope@1 are explicit
same-team families, separate from reserved cross-team binding@3/native-admission@2.
Original bootstrap event@1, development native event@2/internal-command@2/
native-admission@1 and scope@2 stay literal. Selected reviewer native records use
native-review-event@1/native-review-command@1/review-admission@1. Readers derive
both complete attempts, callbacks, subject/contributor/order, all typed native
rows, heartbeats, current CAS and terminal/outer owners on the unchanged full-call
budget. Partial/foreign/orphan/mixed graphs and hidden descendants refuse.
Schema(data=False), cold/reopen, ownership/recovery and drain compose these same
readers. No DDL/reference/type change or trusted after-image/cache is introduced.

The role-selected workflow-coding-review@1 supplement pins actual reviewer
identity, release/mapping/revision, fixed repository/base/head/PR and original
developer INTEGER result. Existing repository tools supply independent inspection;
metadata grants no rights. Authentic review output is retained with native
terminal/cleanup evidence; the run remains held for the unimplemented later edge.
Historical workflow-coding-run@1 remains literal. Progressed metadata uses closed
workflow-coding-run@2 with the same field inventory, current_revision1,
code_review/held state and ordered event metadata, without private findings.

This is source implementation only. F1/F2/F3/F4/F5/F8 behavior, native settlement,
negative/red-green/mutation/restoration/repetition/executable QA are UNEXECUTED;
no test/native experiment/lane was allocated and no old-root waiver applies.
Python units RETIRED; general integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342. Required exact-head all/hooks/hosted checks are lint/static only.
ROOT12299 retains genuine disposable OrgState/native/start/readiness/proof and
same-final-head independent execution before merge. FAILED12298 uncertainty,
separate runtimeROOT12293, adverse receipts and draftPR1134 remain unchanged.
<!-- /TASK12488 / ROOT12299 first ordered edge -->


<!-- TASK12502 / ROOT12299 approved review to QA -->
## Disabled approved review to independent QA source

ROOT12299 step29 / Founder THR320seq4/18/27 extends the disabled revision1
engine through ONE independent qa attempt. This section extends the preceding
first-edge checkpoint. Readiness/require_ready remain unconditionally disabled;
final-manager allocation, finalization, settings/cross-team binding@3 and all
merge/enablement effects remain unimplemented.

workflow-coding-stage-output@1 additionally admits stage=qa with exactly
PASS|REVISE|BLOCK and verdict equal to outcome. The existing exact key inventory,
canonical duplicate-free JSON STRING summary, strict native types/revision1,
nonblank findings<=16384 UTF8 bytes, real own task/SID/INTEGER result and exact
repository/object-format/B/H/branch/PR remain required. Development and reviewer
syntax retain their literal meanings. Findings are independently authored.

Only the authentic completed reviewer APPROVE plus successful/quiescent real
host and positive original tracker/task-tail/worktree/settle_live/native close/
move-release settlement can advance. Callback persistence alone, unusable or
nonapproval output, unknown/failed/blocked ownership and historical held reviews
cannot allocate QA. A separate current-authority transaction reselects the whole
graph, original developer contribution and independent reviewer result, mapping/
release/effective four distinct roles, qa_engineer principal/team and existing
repository scopes, Pause/move/capacity and pinned attempt/revision/event limits.

New workflow-coding-event@4 / edge-command@2 / edge@2 / edge-admission@2 encode
review_staged (code_review+APPROVE), stage_queued and advance in order, with ONE
native uncommitted QA task/attempt/scope in the same business transaction. These
use the existing exact DDL/event kinds. Full independent target validation
precedes caller COMMIT; notification follows it. Exact original-result/CAS replay
returns the original allocation. No generic self-committing delegate, I/O, await
or notification occurs in the synchronous business writer.

workflow-coding-qa-scope@1 pins actual QA task/principal/team/revision/work and
both original developer and reviewer attempt/task/SID/INTEGER result/outcome/
exact subject, release and mapping. The role-selected workflow-coding-qa@1
supplement carries that scope and explicit PASS|REVISE|BLOCK instruction, never
a development/reviewer instruction or legacy task alias. Native QA uses separate
native-qa-event@1/native-qa-command@1/qa-admission@1 families, retaining actual
claim/host/heartbeat/callback/terminal/outer proof and honest causes. A usable
settled QA assessment emits event@4 review_staged for stage=qa and unconditionally
holds, including PASS, pending the separate final-manager increment.

Literal bootstrap/development/reviewer/event@3 and historical review_staged-held
families stay unchanged. New readers independently derive all THREE attempts,
scopes, callbacks/subject/contributor/order/CAS and complete typed native rows.
Only a provisionally discovered event@4 QA child is excluded from earlier
admission snapshots; full ordered reconstruction must prove that exact allocation
and refuse partial prefixes, extra/foreign/orphan/dual owners and mixed formats.
All traversal uses the enclosing cumulative inventory allowance; no trusted DTO,
global cache or pre-write after-image replaces fresh post-write validation.
Current/cold/schema(data=False)/ownership/recovery/drain compose these readers.
Closed workflow-coding-run@3 projects qa/held, revision1 and the existing public
field inventory/event metadata; run@1 and run@2 stay literal, with no findings
exposure. Recovery may notify committed queued QA or reconcile a settled eligible
reviewer; it never relaunches a settled SID or repairs uncertain ownership.

This is SOURCE only. F1/F2/F3/F4/F5/F8 behavior/native/SQL/negative/red-green/
mutation/restoration/repetition/executable-QA remain UNEXECUTED; no tests,
assertions, fixture/support or runtime experiment were allocated. Python units
RETIRED; integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342.
No old-root waiver applies. Lint/static/hooks and the existing OpenAPI summary
generator are the only automated verification here; summary parity does not
prove full response components or native behavior. ROOT12299 retains genuine
native constructor/readiness/remaining-engine/operator/isolation/proof inputs and
failed-child-linked independent same-final-head execution before any merge.
No descriptor grants access/permission. Unknown12298/runtime12293/draft1134 and
all adverse lineage remain preserved.
<!-- /TASK12502 / ROOT12299 approved review to QA -->


<!-- TASK12509 / ROOT12299 live final-manager source -->
## Disabled live final-manager source

ROOT12299 step32 F03/F04/F05 correction / Founder THR320seq4/18/27 extends the same-PR disabled
engine through a fourth actual coding-owned attempt. Readiness/require_ready
remain unconditionally disabled. This source adds no execution, repository
access, credential, settings/@3, control/reassignment, merge or activation grant.
The preceding first-edge/QA sections describe their source checkpoints; literal
historical event@3 held reviews and event@4 held QA (including PASS) remain held.
The repaired event@6/edge-command@4 family with unchanged edge-admission@3 accepts an original
successful/quiescent/positively outer-settled QA PASS while the run is still qa.
It reselects all THREE original INTEGER callbacks, exact task/SID/principal/
attempt/outcome/subject and genuine host/claim/positive tracker/task-tail/worktree/
close/final move-release closure. Current mapping/release/four distinct executable
principals, repository scopes, Pause/move/capacity and original budgets precede
ONE uncommitted manager leaf, ordered review_staged -> stage_queued -> advance,
run CAS and independent complete target validation; notification follows COMMIT.
Original-result replay returns that leaf. The SQL stage remains manager_closeout;
its explicit role/supplement/output/public translation is agent_manager.

workflow-coding-final-scope@2 additionally pins the ONE selected authenticated
repository_reference (repo_key, raw configured-origin SHA256, opaque repository_id,
object_format), selected from the command's pinned mapping/work. Native-manager-event@2/
native-manager-command@2 and unchanged manager-admission@1 bind its real
coding queue/claim/launch/heartbeat/callback/host/terminal/outer lifetime, ordinary
worker_execution purpose and consumed workflow-coding-final-manager@2 supplement.
At session_bound, coding-domain metadata captures workflow-coding-final-workspace@1:
org_slug, literal absolute org_root and workspace_root, principal, task_id,
attempt_id, original invocation_id and session_id. workspace_root is exactly the
actual native OrgPaths.workspaces_dir / assigned principal; cwd/environment and
caller paths supply no root. Every later native observation retains that SAME
original capture. Selected live reads compare it with the actual current owner
and OrgPaths again; independent historical readers reconstruct the full path
and original native task/SID/claim/invocation relationship.
The closed native-manager-command/event@3 family records only held uncertainty
for repaired scope@2. coding_native_preparation_unsettled retains the original
prepared queue identity, producer/task/SID/fingerprint/root and any actual native
observation while workspace/committed claim/host remain absent. The coding queue
capture survives removal of its live slot on the SAME invocation; missing capture
cannot manufacture provenance. coding_native_close_unavailable after binding
retains the SAME original workspace/claim/invocation and explicitly absent current
native close. Independent complete readers join those original identities and
preserve the held cause; neither absence supplies session_bound, launch, cleanup,
selected output or successful settlement. Source-tail observations retain their
separate original identity. Native-manager@1/@2 keep their literal readers and
successful @2 still requires complete original capture and all positive gates.
The source wait/carrier never impersonates this task or reuses an upstream SID.
The narrowly selected GET /workflows/coding/runs/{instance_id}/final-manager
requires exactly session_id=CURRENT, the current original nonrecovery manager's
OWN running leaf/run/attempt/principal/native owner, current Pause/move/authority/
roles/scopes before private decoding and again on return. It returns workflow-coding-final-selection@2 with the selected repository reference,
original workspace capture, pins and three original selected summaries/verdicts.
The opaque repository ID is independently reconstructed by the EXISTING canonical
org/key/origin-digest rule. Current mapping scopes remain a separate gate.
It accepts no bearer; browser/remote access receives no new grant.
The route is exactly excluded from the browser classification. SurfaceAllowList's
default metadata and exact raw/normalized stageDenialCandidate GET matcher deny
its nonempty instance segment and literal final-manager suffix before the
unchanged HomeConnector credential gate. Existing run-metadata GET, adjacent
routes, other methods and custom-policy semantics remain unchanged. This is the
existing decoder's finite semantics, not a broader normalization guarantee.
Source-manager run-show and role publication predicates, SessionTracker and jobs document/legacy
predicates remain unchanged. All file/network work is outside ownership locks.

The LOCAL `happyranch workflows coding finalize --org ORG --session-id CURRENT
--from-file ABS --json` consumes closed canonical workflow-coding-finalize-command@1:
format, operation_key, repository_path (an existing absolute authorized worktree),
instance_id, attempt_id, task_id, session_id, principal, revision=1, subject and
upstream_results. Replace the supplement's repository_path instruction with that
actual path; it grants no access. The command checks literal no-follow workspace/
checkout parents, the selected repo_key checkout and its authorized task worktrees,
Git top-level/common-dir, SHA1 and fixed B/H/branch. It requires exactly ONE raw
configured origin with its exact UTF8 digest matching the selected reference;
missing/multiple/mismatched or rewritten effective origins refuse. No new origin
normalization/adoption policy exists. The actionable GitHub target comes from
that bound origin; descriptor navigation must agree and conveys no entitlement.
It
selects original INTEGER results through the own-manager read, reparses them on
the identical subject and uses no task-prose/legacy recall or caller CI verdict.
The shared waiter is observed ONCE with settle_seconds=0 and timeout_seconds=0;
there is no polling/sleep/job wait. Local stricter selection requires one unique
actual completed SUCCESS for each literal Source Boundary Lint and Web Lint
(Node 24) at H, with actual check/run IDs, run identity and aware observation
and completion times. Missing, stale, ambiguous, pending, inaccessible, failed,
skipped or neutral data holds. Shared waiter/guard/extractor/adapter bytes stay.

Before checks/action, ONE exclusive reservation is made at the original captured
workspace_root/output/<task_id>/coding-finalize-<attempt-digest>/. Workspace,
repository and task-worktree cwd, different keys and authorized repository_path
variants all converge on this SAME directory. Cwd/key/path never selects a root.
Literal parent/alias/containment checks refuse alternate paths. Existing/partial
reservation, including failed recording or a concurrent second entrant, refuses
without another action; preserve first evidence for owner inspection.
intent.json binds the full workspace capture, selected reference, exact raw origin,
command/subject/upstream/selection/check evidence and digests. Full output-path
validation is required; a matching suffix is insufficient. These observations
are not a racing-rename fence, OS security or remote exactly-once guarantee.
The selected live identity/root boundary, local origin/checkout and reservation
inode are revalidated immediately before the action, after guard PR/mergeability
reads. Changed authority, root or reference refuses; current reads are not leases. The reviewed guard calls _gh_perform_merge exactly
once with expected_head_sha=H, ordinary --merge and no admin fallback. Its actual
return/failure diagnostic, native exit where exposed, command argv, timing and
uncertainty are retained. The read-only adapter exposes successful exit0 through
its checked return and original failure diagnostic, not raw successful stdout/
stderr. No stronger transport or exactly-once proof is claimed. Same-session
adapter confirmation requires exact repo/PR/H/MERGED/full lowercase SHA1 commit
and actual aware mergedAt; no local synthetic merge time or empty success.
A failed/lost action remains uncertain even if a separate later read confirms
possible effect; both receipts survive and no second merge occurs. Recording
failure refuses success. No daemon/run-settlement/recovery GitHub I/O exists.

The final callback summary is canonical workflow-coding-final-output@2 JSON
STRING with exactly format, instance_id, attempt_id, revision, stage=agent_manager,
subject, upstream_results, outcome=merged|held|uncertain, findings and evidence.
Its closed bounded evidence binds intent command/digests/path, exact check/run
records and original check/status/run snapshot paths/digests (including holds),
immediate revalidation time, guard verdict, command argv/times/exit/
success/original diagnostic and separate remote observation attribution.
Findings are own/nonblank/at most16384UTF8 bytes. Verdict is null; decisions/job
waits remain forbidden. Merged requires successful command, both exact successful
checks and same-session valid remote confirmation before that own actual callback.
Evidence is an attributable agent receipt, not proof of independent inspection or
an atomic remote authority lease. Missing/malformed/foreign/copied evidence holds.
Only successful host terminal/quiescence and positive native outer closure AFTER
callback permit manager_completed/event@6 to record performed work and run.complete.
Held/uncertain cannot complete. Callback alone, remote effect alone, old ready or
failed/unknown cleanup never completes or triggers a remote action/retry.

Independent readers derive all FOUR original attempts/scopes/callbacks/claims/
hosts/heartbeats/ordered events/CAS/contributions/upstream/final pins and rows.
Provisional later QA/manager discovery is charged to the enclosing cumulative
budget and must close through complete replay; partial/foreign/orphan/dual/mixed/
unknown graphs refuse. New scope@2, selected@2, supplement@2, native-manager
command/event@2, uncertainty-only native-manager command/event@3, edge@4/event@6
and final-output@2 carry the repaired meanings.
Literal old scope/selected/supplement/native-manager/final-output@1 and edge@3/
event@5 preserve original readers/bytes. An old output cannot be promoted into
repaired identity proof; copied foreign reference/root receipts and mixed
families refuse. No backfill or compatibility promise follows. Current/cold/reopen/
schema(data=False)/ownership/recovery/drain compose the same closed reader;
recovery only rediscovers/records owned work or notifies committed queues.
workflow-coding-run@4 projects agent_manager/held/complete and a bounded final
result/task/subject/outcome closeout without findings. Old run@1/@2/@3 stay literal.

SOURCE/STATIC ONLY: F1/F2/F3/F4/F5/F7/F8 native/behavior/SQL/negative/red-green/
mutation/restoration/repetition/executable QA and actual command/native/remote
acceptance remain UNEXECUTED. The one route-classification fixture has four-answer
case evidence; attributable RED/GREEN and native gate proof remain missing.
No new assertion implementation/support/experiment/lane is allocated. Python
units RETIRED, integration INCLUDING callback/lifespan/
SessionTracker SKIPPED24342, no old-root waiver. Normal hooks/nonforce same-PR
publication, clean exact-head lint/static all and full independent review remain.
ROOT12299 owns genuine executable same-final-head QA before merge, actual tool
entitlement and native constructor/isolation/scopes/scratch/installed-serving/
operator/rollback acceptance. No automatic held-manager reallocation exists.
<!-- /TASK12509 / ROOT12299 live final-manager source -->

## Canonical Founder coding settings backend — R2

Founder-only GET/PUT `/api/v1/orgs/{slug}/settings/coding` owns one canonical
DB coding section, two-generation CAS, original-key replay and a genuine human
mapping/publication suffix. No YAML/default fallback, task/SID impersonation,
repository grant or readiness enablement follows. The later page below consumes
this backend without changing its historical families. Complete native
prefix/human suffix/current pointer validation composes through mapping inventory
and cold/schema(data=False)/recovery/drain readers; old literal families remain.
Fresh old-native publication after S1 refuses inside its transaction; historical
exact replay stays read-only. Preserve the original key/body after uncertainty
and reconcile by GET, without reapply or reservation clearance. The full wire,
authority/transaction/budget/error contract and unexecuted acceptance boundaries
are in the ordered-coding-workflow spec's canonical Founder settings section.


## Disabled settings-pinned coding composition — ROOT12568

Current source adds literal start@2, authorization/binding/context@3, development
scope@3 and native-admission@2. Fresh starts require the exact current explicit
human settings publication and mapping/release pins; historical start@1 families
retain their own readers. Admitted continuation retains its original settings and
effective selected roles across later disable/default changes, with current native
authority/Pause/move/host/callback/positive outer closure still mandatory.
Coding-specific cross-team attribution and complete one-budget move projection
are connected; generic execution/reservations and source readiness are unchanged.
No coding start, queue or native launch is enabled. See the ordered-coding-workflow
spec's settings-pinned execution section for exact family, snapshot and evidence
boundaries. Source/static acceptance is separate from genuine scratch/Founder
page/operator/backup/rollback acceptance retained by ROOT12568.

The Founder page is `/orgs/:slug/settings/coding` (TASK12632): org-keyed view,
four org-wide cross-team selectors, one explicit frozen key/body per Save, 409
draft retention with explicit adoption, a provider-lifetime per-org send fence
(survives pane/org switches; fields locked while pending), uncertain-save lock
with read-only GET(original key) released only by a committed receipt (absent
key grants no write), rejected obsolete reads ignored, byte-identical disabled-retain and en/zh-CN copy.
The dedicated same-Founder-auth GET `/settings/coding/options` (TASK12651)
supplies genuine imported release/current authority/configured repository
selectors. The actual yielded capture, complete central inventory and ONE
cumulative budget share one caller-owned read snapshot, without writes, a lease
or repository/Git observations. Unavailable authority has no pin/choices;
invalid history/budget refuses categorically. E0 binds available metadata locally without an adoption prerequisite, then requires four distinct across-team bindings and1..8 repositories with explicit
sha1/sha256 formats. Initial replacements are visibly empty. Saved repositories/
replacements stay intact by default; current-pin adoption is explicit. Options
refresh/error never edits a draft or releases a fence; unchanged disabled-retain
works without options. Rendered MSW checks prove UI/client
behavior only. See the ordered-coding-workflow spec's Founder page section.


## Disabled coding rejection and fresh revision gates — ROOT12568 C05/C07/C08

Founder320seq4/18/27 and ROOT12568 step23 extend the earlier r1 checkpoints:
authentic positively settled REQUEST_CHANGES/REVISE returns to ONE fresh r2/r3
developer on the SAME admitted work/base. New head requires fresh independent
review and only its APPROVE starts fresh QA. All original findings/contributors,
result-absent authors, overrides/across-team bindings and lower limits remain;
BLOCK/failed/unusable/unknown holds. No allocation beyond original revision3 cap.
current_revision tracks fulfilled content; target_revision selects actual new work.
Sequence/generation/CAS/host retry remain separate. Callback alone never advances.

Explicit new event@7/edge@5/edge-command@5/admission@4, revision-scope@1,
revision-native event/command@1 (uncertainty-only@2) and admission@1,
stage-output@2/development-review-QA supplements@2, final-scope/selection/manager/
output@3, LOCAL finalize-command@2 and public run@5 preserve literal old families
and held history. Whole saved/native/current/cold/schema(data=False)/recovery/
drain closure independently derives original callbacks/scopes/tasks/contributors/
current exact subject on ONE aggregate budget, with no trusted after-image or
per-revision reset. Complete validation precedes caller COMMIT, notification after.
Final manager still performs only its own authorized LOCAL expected-head/non-admin
action and independent remote observation before its genuine FINAL callback;
no daemon-owned effect/recovery is added. CLI/served unions carry the new closed
families; browser/native exclusions and document H/I remain unchanged.

Readiness stays false. Source is distinct from installed/loaded/enabled acceptance.
No DDL/auth/permission/grant/live operation/cleanup. Behavioral/SQL/native/proof
NOT RUN; units RETIRED; integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342; Swift missing proof UNEXECUTED/FOUNDER-EXEMPTED/NONBLOCKING320seq93.
Fresh FULL independent review and separately root-routed scoped QA remain.
Control/reassignment/operational and real scratch/Founder/across-team/override/
two-org/backup/rollback/live-manager acceptance stay root-owned. Exact encodings
and active/historical boundaries: ordered-coding-workflow spec, same section.


## Disabled settled no-action manager successor — ROOT12568

A NEW genuine manager attempt may follow only a newly recorded complete bound
held callback whose validated effect evidence affirmatively records a pre-action
guard refusal, no command/remote effect and no action revalidation, AND original
successful host, positive source/outer settlement and no unknown native owner.
Partial intent, callback-only, unknown/preparation/close/no-handle evidence,
uncertain action, merged/complete/cancelled work and literal historical held
families cannot enter. Unsupported held subtypes retain their original owner.

The same bound currently eligible manager receives one new task/queued attempt,
same content revision/subject/reference/three original current upstream results,
predecessor identity and all retained contributors. Stage sequence becomes2;
role generation/CAS/events increase independently. Same-principal retry remains1
and original lower cumulative limits remain; no principal reselection, revision,
host retry, source revival, settings remap or budget reset occurs. Current
settings/authority/ancestry/Pause/move/independence/capacity are revalidated in
one caller-owned writer. Exact accepted original-result replay returns the SAME
successor before fresh capacity checks. Full independent target reconstruction
precedes COMMIT; notification follows, and cold recovery rediscovers that queue.
Superseded callbacks cannot settle the current attempt.

Additive event@8/edge@6/edge-command@6/admission@5 and final-scope@4 encode the
new hold/transition and predecessor. Successor-native event/command@1,
uncertainty-only@2 and admission@1 preserve native ownership; final-manager@4
is the actual consumed supplement. Public run@6 retains field inventory and
allows content revision1..3 for a successor; CLI/served schema share its reader.
Selection/output remain literal @2/r1 and @3/r2..3; LOCAL finalize-command
remains @1/r1 and @2/r2..3.
Complete saved/native/current/schema(data=False)/cold/reopen/recovery/drain
readers reject mixed/orphan/fork/incomplete history on ONE cumulative allowance,
including target rereads. Old held/native/r1..r3 bytes and meanings remain.

The new actor independently rechecks its repository/base/head/results/checks and
current entitlements in its OWN live SID. Only that actor may perform its fresh
expected-head non-admin action and independently observe remote MERGED/full
actual commit/aware time BEFORE its own callback. Never reuse a predecessor
intent, reservation, action or receipt. The daemon performs no PR I/O or effect
recovery. Readiness/require_ready stay false; no allocation is executed here.
Source/static delivery is separate from installed/loaded/enabled acceptance.
Native/SQL/workflow/CAS/replay/rollback proof is NOT RUN / PROOF NOT RUN without
an entitled genuine venue. Python units RETIRED; integration INCLUDING callback/
lifespan/SessionTracker SKIPPED24342; missing Swift proof UNEXECUTED /
FOUNDER-EXEMPTED / NONBLOCKING320seq93. Fresh FULL independent reviewer is next;
root separately routes QA. Deferred-native reconciliation,
reassignment and operational/scratch/Founder/two-org/rollback/live acceptance
remain separately bounded root work. No DDL/release/auth/permission/live effect.


## Coding-owned cancellation — ROOT12568 C11/F5/F8

The existing authorized task-cancel route now composes a coding cancellation
writer even while coding admission is disabled. Under the real DB lock it
rediscovers the target/cascade and complete affected coding graphs, retains
terminal tasks, and atomically writes native cancellation rows, original
`task_cancelled` audits, run/attempt fences and closed cancellation events.
A direct coding leaf stops its complete graph; an ordinary ancestor may stop
multiple graphs. A later ordinary ancestor stop also commits when every coding
graph was already cancelled, with or without ordinary siblings. Previously
cancelled graphs stay in the complete closure: their terminal tasks, results,
events, causes and native owners remain intact, with no second fence or control.
For G1 cancelled, then ordinary P cancelled, then higher live Q, retained
coding discovery crosses terminal ordinary dependency intermediates independently
of the live mutation walk. Only authenticated coding dependency paths are
followed below a terminal ordinary row; newly captured rows there must be
terminal and newly discovered graphs must have an earlier genuine cancellation.
Hidden live work refuses rather than expanding legacy mutation semantics;
unrelated ordinary subtrees remain untouched. P and G1 retain their exact rows,
while Q and its normally reachable live ordinary siblings receive their sole
overlay. Multiple retained graphs and a normally reachable fresh G2 compose in
the same operation; only G2 receives a fresh fence.
When no fresh coding graph exists, the distinct task-scoped audit action
`coding_ordinary_cancellation` carries `workflow-coding-ordinary-cancellation@1`:
the original command, exact ordinary task audits, operation digest and earlier
genuine cancellation-event anchors. It changes no existing event family or
`task_cancelled` audit shape/scope. Independent readers close original rows,
causal membership, retained graphs and strict audit ordering, then derive only
the ordinary-row overlay and freshly compare every target before COMMIT.
Retained coding task originals compare at that later operation's independently
reconstructed boundary, including admissible native observations already present
there. The authenticated event chain and heartbeat audit ordering must agree
with the cancellation capture interval; ambiguous or inconsistent order refuses.
Every later native suffix still replays in full to exact current task/SQL equality,
including executor_pid. Reader-derived boundary copies and traversal consume the
same original allowance; no captured-row trust, field exemption, duplicate stop,
native/effect clearance or history rewrite follows.
Reverse coding/causal dependencies are discovered separately from requested
mutation cascade through the complete authenticated start and edge captures.
A point stop of a coding-dependent ordinary ancestor refuses before any task,
audit, ownership, control or tail mutation, including cancelled graphs and deep
ancestry. Ordinary-only point cancellation retains its existing behavior.
Partial/foreign/orphan/mixed or oversized closure refuses;
complete independent target validation shares the original cumulative allowance
and precedes COMMIT. Existing opaque native/prepared controls and worktree/tail
ordering follow outside the writer. Coding controls never fall back to a PID.
Duplicate cancellation allocates no result, task or new control invocation.

`workflow-coding-cancellation-command@1` records the original target/cascade,
actor/rationale/time, complete affected task originals, coding owner IDs and
actual native owner snapshots. `workflow-coding-cancellation-event@1` stores
that closure once with the original audits and previous-event/CAS anchor;
readers derive every resulting task/run/attempt row independently. Closed
`workflow-coding-cancelled-native-event@1` / command@1 admit only original
handle/PID/terminal/outer observations. A cancellation is a business stop,
never native quiescence, remote-effect absence, preparation cleanup or success.
Affirmative owned queued prelaunch history is distinct from an absent handle.
Missing/failed controls, unclosed preparation, reserved-no-handle, stale journals,
unknown effect or missing terminal/outer proof retain the original owner.
They also set the existing conservative root-tail teardown-incomplete disposition,
including genuine prepared/unknown entries with no control/PID. Successful
control return or absent PID is not settlement; coding batches retain ordinary
outer tracker ownership too. Positively settled original owners keep their
existing disposition. Ordinary-only and document draft/F5 cancellation retain
their existing behavior.
Fresh callback/launch/stage/successor/merge admission is fenced. A precommitted
original INTEGER/task/SID callback may drain or replay exactly, read-only,
without advancing. Tasks/runs remain cancelled when later native facts settle;
no finalizer reservation, historical result/subject/contributor/effect evidence,
generation, lower limit, age or uncertainty is reset. No daemon PR I/O occurs.

Saved/current/native/schema(data=False)/cold/reopen/recovery/drain consumers
share the independent closed reader, including complete cross-graph audit/task
closure. Start ancestry and every development/review/QA/final/revision/successor
edge retain original admission predicates and captured bytes. A changed external
row creates an unresolved equality obligation, resolved only after ALL independent
batches close: freshly read current SQL must equal the sole authenticated
cancellation overlay of that exact original row. No partial census, trusted
after-image, mutable-field exemption or reusable validation cache is accepted;
all copying/traversal/reads and post-write reconstruction share the ONE original
aggregate allowance before COMMIT. Public `workflow-coding-run@7` keeps the existing field inventory,
explicit cancelled state and cancelled-or-uncertain current attempt; the CLI
and served route consume the same union. Literal old held/r1-r3/native/document/
then histories retain their own meanings. Deferred native recovery still refuses
`coding_native_recovery_unavailable`; reassignment remains separately bounded.
The current supplements require stopping fresh work on cancellation and retaining
the original callback/effect/native owner, without granting a new control tool.

SOURCE delivery only: readiness remains false, installed source is untouched,
loaded identity UNVERIFIED and enabled acceptance absent. Native/SQL/workflow/
race/causal proof NOT RUN / PROOF NOT RUN without a genuine authorized venue
(THR320seq99). Python units RETIRED; general integration INCLUDING callback/
lifespan/SessionTracker SKIPPED24342; all missing Swift proof UNEXECUTED /
FOUNDER-EXEMPTED / NONBLOCKING320seq93. Static checks prove no workflow behavior.
Founder THR320seq136 authorizes source recovery under sole ROOT12727 without
reviving failed ROOT12568 or predecessors. Genuine four-stage execution,
schema-import/operator/scratch availability are no longer source-merge
prerequisites. Real workflow/Founder persistence/across-team/two-org/backup/
reopen/rollback acceptance remains NOT RUN/open and required before ordinary
work or activation. Historical checkpoint evidence and failures stay attributed
to their original owners/heads. Fresh FULL independent final-head review and
separately root-routed scoped QA remain required; the actual entitled final
manager alone owns expected-head/non-admin guarded merge and independent remote
confirmation in its own live session before callback. No schema/auth/permission/
grant/restart/deploy/live experiment, self-merge or cleanup is authorized here.

## Guided human host coding setup — ROOT12877 / THR320seq162

From the reviewed published host checkout, a genuine human terminal may run
`python3 scripts/setup_coding.py --org happyranch`, then use Settings → Coding.
This explicit coordinator reuses existing lifecycle/build and saved-definition observation
owners. Normal OrgState.load atomically adds inert coding storage without
upgrading document layouts; the coordinator invokes no migration chain. Managed markers,
workspace/native ancestry, current all-runtime owners and unavailable complete
present observations refuse before setup effects. It uses the existing source environment
for read-only F/E/G/H/I/J preflight, never live Database/OrgState constructors.

After positive maintenance clearance, human confirmation and revalidation,
private durable command intent precedes stop; result/uncertainty receipts retain
observed exits and bounded stream digests without copying lifecycle log bodies.
Receipt-only/partial recovery directories are incomplete; only verified, durably published manifest.json accepted in this invocation
establishes a complete backup. Failed receipt persistence prevents later effects.
The start-reported uv wrapper PID and daemon.pid Python PID remain distinct:
checked executable/source/home/custody, fresh start identities and direct ancestry
must agree, then status uses the actual daemon PID/port. Both identities are
revalidated through serving/import; missing/moving/reused facts hold.

Private complete recovery inventory and WAL-inclusive SQLite backups precede
locked dependency/UI preparation and controlled start. Full post-start coding
readiness is validated alongside the actual preserved document layout; no
F/E→G→H→I→J writer is part of normal coding setup. Stop stale/KILL/unknown closure, failed backup,
source drift or uncertain writer stops the sequence without automatic restart,
restore or replay. This is cooperative maintenance, not a new daemon lock.

Actual serving boot/source/org/UI observations precede read-only saved template
availability. The normal coordinator lists genuine teams, then the existing
team-scoped template listing, and compares the saved fixed definition, IDs,
digest/pins and truthful system/human publisher. It sends no importer POST and
creates no human operation/intent. Genuine human originals retain their exact
readback/custody, without replay or new key. The existing twelve loaded-object
observations, OrgState alias and import-store binding checks remain required;
they do not attest the new registrar/resolver or prove startup invocation.
Missing/malformed/moving coverage and unrelated observation errors still hold;
health200/disk HEAD/index equality are insufficient.

The command chooses/saves/enables no roles or settings. Select
repositories/formats and four distinct eligible org agents across teams with an
eligible final manager; keep Admission Disabled, save/reopen, and use Check
original save for uncertainty. Source/static delivery is not host acceptance.
Real workflow, Founder persistence, scratch/rejection/revision/control,
cross-team/two-org, backup/restore/reopen/rollback acceptance remain NOT RUN/open
before ordinary coding use. See the ordered-coding spec and runtime/CLI guides
for flags, recovery receipts, explicit pinned bootstrap and observation limits.

Under ROOT12912 / Founder THR320seq177–178, historical job/receipt/launch
uncertainty alone is not a migration-maintenance refusal. Preserve all original
labels, ownership, keys, bytes and adverse evidence without cancel/relabel/settle,
journal clearing, replay or claims of never-ran/quiescence. Account for `_pending`
and `_archive` boundaries only: do not descend/read/copy their members, archives
or OP evidence, move or delete them. They are untouched migration exclusions,
not backup targets. A real present writer involving them still conflicts; unknown
ordinary/partial orgs and actually affected uncopied material still hold.

The human-controlled complete cooperative window begins before constructor
commands/RO effects and any preceding host update. It covers all affected
registered runtimes, normal orgs, shared-daemon stores, source/environment users
and other hosts through backup, preparation, every migration, controlled restart,
import checks and return. Repeat supported current owner/process/preparation/
lifecycle/identity checks before and after effects; retain SQLite reservations.
A one-time census, PID absence or human confirmation alone supplies no exclusion.
Missing present attribution holds. No cross-host lock, hostile same-user or hard
real-time guarantee is claimed; failure retains safe custody for Engineering.

Running entry requires complete trustworthy current observations and one original
stop/intent/result/status/process closure. A failed/uncertain stop in this invocation
never falls into stopped success. Independently selected positive already-stopped/
excluded entry uses existing lifecycle/process/cooperative controls, skips stop
truthfully and queries no stopped HTTP service; it reconstructs no old shutdown.
Fresh distinct wrapper/Python checks remain mandatory after original start.
Current failed views with opening preparations[], null cutoff/no installed
identity are incomplete and hold running entry. Engineering/serving must establish
a supported complete running observation, or the human operator must establish
genuine positive stopped entry with existing controls. No error bypass or memory
endpoint/reset is supplied. Final selected loaded-source/UI acceptance remains
after restart, with all twelve actual selected objects, full D1 list validation,
closed identity/budgets/bookends and human rendering before import.

Stop's shutdown effects precede the full stopped backup; receipt-only evidence
is incomplete. Full affected DB/config/source/environment/UI/shared-daemon recovery
inventory, WAL-inclusive SQLite backup/integrity/FKs and verified copy hashes,
identities, restore mappings and original credential custody precede preparation
or schema mutation. No WAL/SHM reset/checkpoint occurs. Accept the complete manifest
only after verification and exclusive durable publication; retain its identity/
digest and require that accepted custody before later stages. Existence alone is
insufficient. Each original writer0 requires its exact post-check0 and full target
validation before accepted-layout advance. All earlier pre/post-reservation calls
and all three I→J idle calls, including post-DDL, use current maintenance selection;
the nonmigration roster caller retains its original semantics. Full schema/data/
history/types/rowids/bytes/release/FK validation, original FK policies, RO→RW
identity, reservations/offline bookends and rollback remain unchanged. Behavioral,
backup/restore/reopen/rollback and operational acceptance remain NOT RUN/open.

C06-B/C07 public recovery uses the existing guided command, without a prewritten
inputs/roles/selector/mapping ledger. Setup first discovers complete literal
root/parent/object metadata, membership, inode aliases and allowed-link/referent
closure. Separate current-custodian questions bind exact machine membership to
known/mixed/unknown public/package/generated roles, private separation and all
writer/shared-inode-user control. Exceptions name exact paths/subtrees and only
deny admission, including innocently named private material. Unknown/disputed or
conflicting roles refuse unopened. These are genuine present facts, not an
access grant, proof of human knowledge or a historical installation certificate;
no such host knowledge/control is supplied by source delivery. Ordinary nonmatched
shared DB/config/state stays with its existing backup owner; ordinary private
business state is not relabeled public code, and credential exceptions still deny.

Reference custody/public role precedes reference content access. When installed
matches require public inputs, the conversation selects a literal private local
archive directory (default `<backup.parent>/public-origin-inputs/<source-tree>`)
and an exact prior-reviewed full source commit only if OLD locks differ. Setup
produces exact lock/member joins, dependencies and a retained recovery plan;
humans supply no per-file identities or archive mapping JSON. Missing-input
output points to exact reviewed lock URLs/hashes. Only a human later obtains
anonymous exact-lock public archives. Setup adds no download/network client,
cache/home/auth-config scan or installation before complete recovery.

Candidate role AND independent pinned Git blob, whole-lock-verified ordinary
wheel/tar member or supported CPython expectation precede candidate open, even a
header/hash. Names, package prefixes, RECORD, archive membership, UID/nlink or a
generic yes alone never admit. The finite CPython recipe uses independent paired
public source, exact cache tag/filename, optimization0/1/2, current compiler/marshal
configuration and magic/header flags0/1/3, exact source timestamp/size or hash, and
in-memory compile/serialization; the paired actual source is verified first.
It never imports/unmarshals/executes candidates or writes caches. Node selects the
exact longest lock installation path and safe ordinary tar member. Unsupported
wheel/platform/generation, unsafe namespace, mismatch or ambiguity refuses;
reference bytes and rebuilds never replace ORIGINAL recovery copies.

Admitted reads/copies hold no-follow parent/file descriptors with full metadata
and path bookends, byte equality and exclusive private independent copies.
Stable source nlink>1 requires actual complete shared-user control; no link-breaking,
chmod/chown or preparation repair. Record unobserved external aliases honestly;
recovery promises selected content/paths, not their topology. Allowed env/node/raw
CLAUDE→AGENTS links, including lib64, retain every intermediate hop and close
through admitted originals or the positively untouched usable selected external
base interpreter/chain. Other unknown
external dependencies refuse. The existing daemon token stays unopened/uncopied
in original custody, with metadata rechecks and an exact unchanged-owner effect
reason; no blanket credential exception exists. Any affected genuine credential
blocks completeness: return its exact object/custodian/proposed operation and
C07 remover to Engineering before access.

Retain ALL actual OLD source/env/node/dist/feature state and affected shared-daemon/
runtime/ordinary-partial-org DB/config/referents. Existing WAL-consistent SQLite
backup/integrity/FKs/mappings remain; SHM readmarks are not byte-immutable evidence.
Shared cooperative bounds are200000 entries,512MiB archive bytes,1GiB expanded
members,64MiB selected member,8MiB compiler source,1MiB lock input and600s origin
work; no hard RSS/OS-I/O time or hostile-user fence is claimed. Closing verification
recomputes full original membership/coverage, then verifies retained inputs/plan/
copies and exclusively publishes/readbacks the durable manifest. Every later
uv/npm/build/migration/start/import/return gate authenticates complete accepted
OLD recovery custody, including reference/copy membership; after legitimate C07
replacement it does not compare the new environment with OLD contents. Any drift,
missing copy/reference, publication or later failure clears invocation acceptance,
retains the first cause/partial originals/receipts and secondary persistence failure,
and permits zero next effects, with no repair/retry/new key/restore/replay/restart.
Behavior/recovery proof remains NOT RUN/PROOF NOT RUN; setup NOT READY, Coding Disabled.

Every script-originated refusal has an incident/stage, last completed stage and
accepted layout, truthful attempted/uncertain effects, and one instruction:
`Send this output to engineering_manager`. Main owns the in-memory context before
the unchanged host guard, including partial constructor failures. Pre-custody
detail exists only in output; after permitted private receipt custody, an
exclusive bounded diagnostic is called saved only after write/readback/fsync.
No arbitrary exception/argv/environment/transport/log/credential bodies are
printed. Persistence/reporting failure preserves the original cause/effects and
uses a once-only fallback; all-sink failure, kill and pre-main failure have no
delivery guarantee. Nothing-was-changed claims require positive pre-effect proof;
constructor temporaries and RO SQLite sidecars prevent a global inference.

The serving preparation field is the complete original five-field reference LIST:
only valid empty permits the next original check; valid nonempty retains exact
private references and holds, including observation_pending/null-cutoff facts.
Missing, wrong-container and malformed rows (including a bad tail) refuse without
coercion/filtering/deduplication. Closed identity, boot/cutoff, source/runtime/org
and every independent error remain mandatory. New selected identity may require
fresh genuine memory qualification; no epoch/collection acceptance is performed.

Receipt-only/partial custody, stop attempt versus positive closure, partial/full
backup, individual preparation commands/UI, every attempted migration versus last
accepted layout, start/pair/serving, and original import file/intent/send/receipt/
readback remain distinct. Failed commands remain failed; no next effect, automatic
restart/restore/replay or new key follows uncertainty. Source delivery leaves
Coding Disabled and real host/workflow/Founder/backup/recovery acceptance open.
