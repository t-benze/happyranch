# Sequential Coding U5: current evidence and local Final capability

2026-11-11; THR-336 / ROOT TASK-15165; U5a maker TASK-15200. Source base
`c9732f07f79cd74e04a41777a6f1ebae57079704` includes U4b3 PR1373.
U5b1 maker TASK-15222 uses base `4d662ef96d933bb0cdd86e4d2761e116c3d70c24`
(U5a PR1380). This is the full U5 design and its reader/policy plus assessment
producer merge units. It supplies
no live admission, installation, migration, deployment or runtime acceptance.

## Authority and preserved decisions

Plan rev3 `engineering_manager/output/TASK-14461/implementation-plan.md` §U5
(SHA256 `adb23e805373d0694c4ad79f1847c50d5f7fabcb20fb193cb110f6dd6cee06e7`),
R-M2-1 `output/TASK-15144/r-m2-1-u4-u9-rescope.md` (SHA256
`49d6cb83a575ae5e15b9b5cb6415371052cc66d54331cb15e3f238ddc01c153d`, “U5
unchanged”), and canonical Shared Artifact
`product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md` (SHA256
`96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`)
control this unit. PRD F3/F4/F5/F6/F8 and AC5/AC6/AC8/AC9 apply.
Founder THR-336 msg131, “ok access 1”, selects ACCESS-1 from msg130.

ACCESS-1 means the daemon rechecks **only current authority** before each later
disclosure/publication/action: role eligibility, fixed binding, active principal
and unchanged repository/origin/base/scope. Effective repository access is the
selected agent's own operation in its live session: reviewer/QA exact-head fetch,
Final manager expected-head non-admin guarded merge. Its failure is BLOCK ->
Held, distinguishing denied from unverified. There is no four-role checkpoint,
new credential, access observer, daemon GitHub/network I/O or OS containment
claim. Preserve U4a/U4b2 clone-metadata/SHA1/origin/base preflight unchanged.
See the [U4 owner/spec](2026-11-11-sequential-coding-u4.md).

Preserve F-1=(i), Decision1=B (49152-byte JSON; document 1 MiB), Decision2=1,
SB-1=(a), all earlier failed/cancelled tasks and consumed allowances, V3 window
ending `2026-11-23T08:48:38Z`, and held PR1252. U5 supplies no replenishment,
thread notifications, live config/admission/roles, install/migration,
deploy/restart, genuine Coding scratch (V5/M4) or effect recovery. Merged is
separate from deployed. A newly required product/schema/auth/permission or
Founder-reserved change stops for the manager; none is introduced here.

## Split and caller inventory

**U5a implemented here:** closed assessment syntax, an unmounted SELECT-only
current-evidence reader and pure capability derivation/refusal classification.
It returns transient evidence admissibility, never an executable Final selection,
intent/action or durable capability. A refusal returns no admissibility value.
The current-evidence Final reader remains unmounted; its settled-result helper
is now reused by U5b1. Final outputs keep their unavailable hold.

**U5b1 implemented (TASK-15222):** attach `independent-code-review@1` APPROVE
and `code-qa@1` PASS composition and addressed assessment disclosure through
`SequentialDispatcher`. The producer consumes U5a's closed syntax and settled
native authenticator under the original current-authority ownership. Negative
REQUEST_CHANGES/REVISE/BLOCK callbacks retain their ordinary no-output path.

**U5b2 split:** TASK-15240 implements U5b2a unmounted live-owner selection
and local journal records/readers (details below). U5b2b still owes the common
local CLI finalization core; adapt historical `cli/commands/workflow_coding_finalize.py` without changing
its admitted families; use `runtime/daemon/pr_ci_merge.guarded_merge` only as the
existing locally called guard requires; selected-head lint observation and owned
intent/action/remote-observation validation. `guarded-repository-merge@1` success
continues to refuse with the existing unavailable-output durable hold. Assessment
witnesses grant no partial Final selection, intent, action or merge permission.
No producer may precede its compatible reader, current-authority checks and
cancellation boundary.

| Owner / symbols | Callers, consumers and responsibility |
| --- | --- |
| New `sequential_coding_policy.py`: `CodeAssessmentDeclaration`, `read_code_assessment`, `SettledCodingResult`, `CurrentCodingEvidence`, `CodingAdmissibility`, `mandatory_coding_assessments`, `derive_coding_admissibility`, refusal enum/exception | U5b1 output producer consumes the assessment syntax; the Final SQL reader consumes policy. No executable Final consumer. All data classes are in-process projections, with no authenticated Boolean or executable capability. |
| New `sequential_coding_evidence.py`: `read_current_coding_evidence_uncommitted`, `_pinned`, `_read`, `_settled` | U5b1 `_settled_input` calls `_settled` for original upstream callback/host/outer joins. U5b2a reuses `_read` under its same original allowance and live-owner boundary; no route, CLI, settings consumer or registration. SQL SELECTs and pure validation only. |
| `sequential_history`: `_run_history`, `_outputs`, `_native_rows`, `_ReadBudget`; `sequential_run_validation`: `_selected_layout`, `_read_selected`, `_selected_settings`, `_prior_state` | Existing complete/selected readers and engine keep ownership. U5a replays the whole selected run, not incremental trust in old result/envelope writes. Original admitted pins are read without selecting today's settings. |
| `sequential_business_hold.hold_body`, `_proof` | Existing hold writer/readers remain owners. U5a reuses their SELECT-only native INTEGER callback + successful terminal + positive original outer authenticator, discarding the reconstructed hold body. It writes no hold. |
| `sequential_coding_output`: declaration, `coding_binding_context`, `_assessment_declaration`, `_settled_input`, `_assessment_inputs`, `build_coding_witness`, `authorize_coding_input`, `coding_assessment_supplement`; `sequential_repository_subject.read_repository_subject` | U5b1 extends the existing output/disclosure owner. Reuse exact declared change syntax, fixed bindings/current eligibility, original committed subject and U5a settled proofs. Do not adopt historical Coding repository capability or reobserve Git. |
| `WorkflowAuthorityCoordinator._validate_admission_uncommitted` | Existing sole captured-authority validator. Caller captures outside locks, owns the original authority transaction/locks; U5a validates before and after derivation without file/network reads. Missing/stale authority is unverified; proved inactive/ineligible binding is denied. |
| `SequentialDispatcher._compose`, `_advance`, `node_instruction`, `outer_settled` | U5b1 extends supported output types, context/proof preparation, verdict matching and trusted assessment supplements. Existing rollback-then-Held owner and native/outer COMMIT order remain unchanged; no callback/hold writer is added. |
| `cli/commands/workflow_coding_finalize.py` (`_selected`, `_execute`, `_checks`, `_local_subject`) | Historical local Final adapter, no sequential consumer today. U5b2 extracts/reuses a common local core while keeping immutable historical command/selection/output interpretation. |
| `pr_ci_waiter.wait_for_ci`, `pr_ci_merge.guarded_merge`, `_gh_perform_merge`, remote confirmation | Called only by the selected Final manager's local CLI in U5b2. Their module location grants no daemon effect authority. No new job/recovery executor. |
| Engine/reducer/cancellation/native Pause and ownership | Preserve allocation/CAS, invalidation, output caps, cancellation, native tail/outer proof and independent merge-observation versus execution-settlement owners. U6 still owns mixed-history closure; U7 public transport; U9 fresh-start cutover. |

## U5a current-evidence reader and policy

`read_current_coding_evidence_uncommitted` accepts original org/run/Final
task/principal/SID, canonical declared change bytes and original authority
coordinator/capture. Its caller owns coherent raw SQLite values, admission
transaction and locks throughout; no await or capture I/O occurs inside it.
This internal reader does not establish actual host liveness. U5b must prove
the original selected live invocation before exposing any executable selection.

The reader compares the complete selected schema, reads immutable definition/
settings/start pins, all visits/events/outputs for that run, and replays the full
prefix with the common reducer. This checks native result attribution, envelopes,
selection inputs, edges, returns, invalidations, counters, event digest chain and
CAS. Current evidence comes from replayed selections, **not Final's declared
review/QA inputs**, node IDs, presumed stage order or newest verdict lookup.
Final's declared change must match the original subject and current epoch.

The selected Final must be the active run's sole running selected
`guarded-repository-merge@1` visit, with matching task/principal/SID, no result,
native in-progress identity, no cancellation/zombie/chain/fanout/job wait,
authentic sequential ownership, and a current registered executable manager
binding. Review/QA/development selected principals retain their fixed active
worker bindings. A stale capture or unavailable profile/authority owner refuses
as unverified; positively ineligible/inactive/missing principal refuses as denied.
This is current authority, never effective repository permission observation.

Assessment output syntax is canonical UTF8 JSON, at most 49152 bytes:
`{"format":"workflow-sequential-code-assessment@1","change":<entire original
workflow-sequential-code-change@1 declaration>}`. This is a subject assertion
only. Approval/PASS comes exclusively from the original native INTEGER callback
and its closed canonical carrier, not an outcome field in this content or an
agent-supplied check. U5b1's producer uses this same syntax/reader.

For every selected development/review/QA result, re-read the original native
callback bytes, require completed status and its exact task/principal/SID,
successful host receipt (supported backend, Boolean quiescence, no survivors),
positive original outer closure with matching invocation/callback/carrier digest,
and completed uncancelled native task. The original carrier's output bytes must
equal the saved output, outcome equal the accepted envelope, and claims be empty.
No unsigned PASS string, saved digest, check list, stale native result, local
process success or remote PR state substitutes for these joins.

Trusted policy unconditionally selects `independent-code-review@1` APPROVE and
`code-qa@1` PASS for every Final capability. A valid develop -> Final definition
omitting them reaches this policy and refuses missing review; review satisfied
without QA refuses missing QA. Rerouted graph/input data cannot waive either.
Require exactly one current development, review and QA result, all same run,
epoch and full declaration: repository ID/key/raw-origin digest, SHA1 format,
base, immutable scope, full head and exact PR repository/number/URL. Stale head,
PR, base or epoch refuses. Ambiguous evidence refuses rather than choosing one.

Retain contributor principals from **all** run visits producing the code-change
contract, including earlier content epochs and invalidated outputs. This is
native run contributor attribution, not a claim to enumerate Git commit authors.
Reviewer, QA and Final are mutually distinct and absent from that entire set;
current developer is in the contributor set. Definition independence omissions
cannot allow self-approval. Current access/authority checks never erase retained
contributor history. Existing intent/action evidence refuses as effect-uncertain;
there is no clearance, replay or second effect after missing/failed recording.

Successful derivation contains exact development/review/QA references, original
carrier/terminal/outer digests, original run/Final identity, definition/start/CAS/
event/input pins and mandatory lint names. It proves neither successful lints nor
merge permission. Parsing/reconstructing/saving its classes confers no authority.
U5b must re-read at later boundaries; no cached derivation is a lasting grant.

Closed refusal causes distinguish missing review, missing QA, stale evidence,
non-independent contributor, authority denied, authority unverified, malformed/
unverified evidence, unavailable Final and prior/uncertain effect. Refusal raises
without returning any admissibility/executable selection or writing any row,
intent or action. U5b must translate these finite causes through the existing
rollback-then-business-Held owner, preserving actual causal evidence; persistence
failure cannot be reported as durable Held. U5a itself does not persist Held.

One original `_ReadBudget` covers selected schema, full replay, native proofs,
copies, parser/policy work and closing validation: cooperative five seconds,
64 MiB charged bytes, 65536 traversal steps and SQL progress ceiling. Existing
row/value bounds remain; exhaustion refuses without partial evidence, renewal or
hard memory/time guarantees. SQL is SELECT-only; no transaction control.

## U5b1 assessment composition and addressed disclosure (implemented)

An APPROVE/PASS producer requires its own completed original INTEGER callback
and matching verdict, successful native host receipt and positive outer proof,
then current fixed active/eligible bindings and the unchanged committed subject.
The entire assessment declaration must equal the one selected `code-change@1`
input, including repository/key/origin/base/scope, full head, exact PR and current
epoch. Missing/ambiguous change input refuses as subject-unverified. Every selected
Coding upstream result is re-read with U5a's `_settled` authenticator: original
callback content/verdict, host/outer digests, native completed uncancelled task,
full output reference and selected content must agree. Selected upstream workers
retain their original binding; reviewer/QA cannot be a selected upstream principal
or any code-change contributor in the run, including invalidated earlier epochs.

Witnesses reuse U4b3's original provenance/content/subject/recipient preimage and
existing check names. REQUEST_CHANGES, REVISE and BLOCK have empty content/claims,
own findings, matching verdict and no domain witness/output; existing reducer
return/hold and consumed-counter semantics apply. Assessment syntax/provenance
never proves semantic truth or the agent's effective access. The trusted supplement
requires the selected reviewer/QA's own exact-head fetch in its live session.

Addressed input rendering rechecks authority through the existing owned writer
before returning any bytes. It validates original recipients, selected reference,
content hash, committed subject/current epoch and original settled assessment,
including equality with any selected code-change input.
Denied and unverified authority stay distinct. Refused publication rolls back and
uses U4b1's sole independent finite business-Held writer with zero accepted output,
verified checks or successor for that visit. Disclosure refusal returns no bytes
and preserves selections/bindings; it does not invent a pre-callback result or hold.
An agent's required-operation failure is its own causal BLOCK callback. Failed
hold COMMIT cannot claim durable Held. No new hold cause, schema, authentication,
permission, remote observer, credential or daemon network operation is introduced.

One original dispatcher `_ReadBudget` covers parser/model copies, decoded
traversal, selected upstream native proof reads, contributor enumeration and
witness/disclosure work. Both the new declaration walk and reused `_settled`
walk charge that original allowance; exhaustion is a reader refusal, outside
malformed-output conversion. No allowance renewal follows a refusal. Native
callback, host, positive outer and sequential COMMIT/queue order are unchanged.

Native Impact: three Python files change. `sequential_coding_output` owns the
new producer/disclosure helpers, `sequential_coding_evidence._settled` charges its
decoded declaration traversal, and `sequential_dispatch._compose`, `_advance` and
`node_instruction` select the added types/supplement. The dispatcher remains
mounted by `OrgState`; task callback, outer/native settlement, engine/reducer,
current-authority coordinator, immutable definition/settings, U4 clone preflight,
Final CLI/guard, admission/configuration and cancellation owners are unmodified.
Risk is assessment subject/provenance attribution and refusal classification;
there is no schema/contract-family or historical adapter change. Targeted caller
search, full B..H/AST inventory and synthetic diagnostic evidence are recorded
in `dev_agent/output/TASK-15222/handoff.md`.

## U5b local Final execution and observation design (U5b2 pending)

1. Prove the actual selected original live Final task/principal/SID and native
   invocation, unchanged subject and current authority. Derive U5a evidence at
   that same boundary before exposing an executable Final selection. A valid
   omitted/rerouted graph must reach policy; an earlier validator denial is not
   the mandatory-review/QA negative. Bind original workspace/repository metadata
   and exact selection pins. No daemon GitHub/PR/network I/O.
2. The local Final-manager CLI core consumes only that exact selection. Reuse
   historical adapter safeguards and revalidate authority/selection before each
   later disclosure/publication/action. Effective access is established by its
   own real operation, never a daemon access observer or synthetic probe. Actual
   denied versus unverified repository failure returns BLOCK -> Held.
3. Locally observe exactly **Source Boundary Lint** and **Web Lint (Node 24)**
   successful at selected H. Preserve actual remote check identities/head/status;
   missing, pending, stale, ambiguous, skipped or neutral is Held. Do not accept
   arbitrary status labels, caller Boolean ci_pass or old-head checks. This
   source task does not execute or claim those future workflow observations.
4. Reserve one original Final-task-owned attempt/intent independent of operation
   key/cwd/path variants, with unchanged original native workspace and subject.
   Persist the exact selected pins, check observations and intent before action;
   preserve partial/failed recording uncertainty. Before the one action, re-read
   current authority, exact selection and immutable local subject. A refusal
   before capability admission creates zero executable selection/intent/action;
   later failed lint/operation preserves its actual local evidence and hold.
5. Invoke existing ordinary guarded merge with full expected head and without
   `--admin`; retain independent review APPROVE, QA PASS, open/non-draft exact PR,
   head and clean mergeability gates. Final manager owns the command, original
   exit/diagnostic and local receipt in its genuine live session, never a job,
   daemon service, old source SID or recovery runner.
6. Independently observe exact repository/PR/URL, pinned H, remote MERGED, full
   actual merge-commit SHA1 and timezone-aware merge time. Never synthesize a
   commit or timestamp. Command failure/loss stays failed/uncertain even if a
   later read sees MERGED; that read is a separate observation. No reissued
   merge, new operation key, admin fallback or automatic recovery.
7. The daemon only validates/records the genuine original callback and remote
   observation. Business merge observation and native execution settlement stay
   separate: only successful original host termination AND positive outer
   settlement permit normal reducer advance/caller return. A remote merge cannot
   clear native uncertainty or fabricate a result. Cancellation fences new work
   and retains already-owned effect facts under the existing owners.

## Evidence and remaining gates

No repository tests are added/changed in either unit. Python units are RETIRED; general
integration is SKIPPED (THR-243 seq42); no ad hoc Product E2E. Required delivery
checks are exact-H `scripts/local_ci.sh all` as an authenticated job, normal
hooked nonforce push, full B..H/AST/radius review, and hosted Source Boundary Lint
plus Web Lint (Node 24) snapshot at H. Independent review APPROVE, scoped QA PASS
and manager guarded merge remain required; the maker never approves/QA/merges.

The disposable out-of-repo diagnostic drives the actual U5a reader/policy using
synthetic authority, callback/host/outer/accepted-row scaffolding. Required cases:
missing review; review satisfied/missing QA; stale head/PR/base/epoch; self-approval
and retained-contributor independence; lost authority denied vs unverified;
omitted/rerouted nodes still mandatory; admissible full evidence at the same
boundary; zero SQL changes/executable selection/intent/action on refusal.
Scaffolding is not native, V3, real agent fetch, successful lint-at-selected-head,
live Final selection/command/merge, genuine Coding scratch, cancellation/restart,
effect recovery, installed compatibility or deployment acceptance. These remain
NOT RUN; preserve historical failures. Handoff records actual diagnostic results,
commands/exits, exact B/H, full changed-file/AST inventory and remaining gates.


U5b1's disposable out-of-repo diagnostic drives actual dispatcher composition,
acceptance/transition, addressed rendering and complete/selected readers with
explicitly synthetic authority, TaskInvocation, INTEGER callbacks and host/outer
scaffolding. Cases cover review APPROVE/REQUEST_CHANGES/BLOCK, QA PASS/REVISE/BLOCK,
stale full subjects/head/PR/base/epoch, denied/unverified current authority, shared
budget exhaustion and continued Final refusal. It supplies no real live fetch,
native workflow, selected-head lint, Final execution, deployment or runtime PASS.


## U5b2a live selection and local journal interface (TASK-15240)

Base `0e78217f2af8306863b31b0ee8d237819e5671e8` includes U5a PR1380 and
U5b1 PR1382. Under the brief's 1800-second split rule, this unit delivers the
**unmounted** live-selection and local intent/observation records/readers boundary.
No database table, column, index, migration, public command or historical family
changes. U5b2b below remains required implementation, not a verification-only tail.

`SequentialCodingFinalSelection.select` captures authority outside ownership,
then takes the existing authority/teams/DB locks, original session binding lease
and admission writer. It proves one original sequential TaskInvocation in the
native live store; admitted/committed/unsettled/launch-observed facts; original
running Pause journal entry and original admission generation; independently
unheld current root control; matching original host request
and supported opaque running handle; active non-recovery task/principal/SID;
no terminal, callback, cancellation or team-move refusal. Original OrgPaths
supplies the fixed manager workspace. Bounded ancestor-pointer and journal-byte
preflight precede the unchanged native Pause reader; no full task briefs are read
by that live-owner preflight. No OS scan, process poll, repository access
observer, file read, Git or network operation is performed in this boundary.
These are current native owner facts, not an OS-level future-liveness lease.
The selection's `pause_generation` retains that invocation's original journal
generation. Pause/Resume may advance the root control generation without changing
the original owner/session/handle or requiring those two generations to equal.
Held roots still refuse selection. Existing or partial reservations stay fenced;
Selection never rewrites the journal, allocates a SID or permits reservation replay.

Inside that SAME boundary, U5a's `_read`/`derive_coding_admissibility` fully
replay the selected history and require original settled independent review/QA,
unchanged full subject and current authority. Valid omitted/rerouted graphs
reach mandatory policy: absent review refuses missing review, satisfied review
without QA refuses missing QA. The current Final binding's configured origin
must still hash to the original committed raw-origin digest. Recheck authority,
original native owner/handle/workspace and cancellation before returning any
selection. Refusal returns no selection/intent/action. The original read budget
covers entry, admission-validator accounting, both authority/native bookends,
U5a replay/policy, projection, serialization and closing validation. Exhaustion
remains refusal; no fresh allowance is created at lock entry.

`sequential_coding_final_records` owns closed internal selection, lint, intent,
command-result and remote-observation models and compatible bounded canonical
readers. The selection fixes native invocation/workspace and all definition,
start, CAS/event/input pins, retained contributors and three exact settled result
references with original carrier/terminal/outer digests. Parsing these records
proves no issuer, effective access, successful remote check or command. Intent
syntax requires exactly the two literal successful selected-head lints, distinct
check identities and aware ordered timestamps; missing/pending/old-head/ambiguous/
skipped/neutral data refuses. The future observer must authenticate actual raw
provider observations; constructing a record supplies no such observation.

`workflow_sequential_final_journal` is an unmounted LOCAL helper. A trusted fresh
selection callback must reproduce the exact original selection before reservation
and before/after intent publication. The original existing manager workspace and
Final task alone choose `output/<task>/sequential-coding-finalize`: operation key,
invocation/cwd/path variants cannot choose another attempt. Exclusive mkdir,
literal directory checks, parent-directory fsync and exclusive fsynced file writes
preserve one reservation and selected/check/intent bytes before any future action.
Existing or partial reservations refuse; errors and cancellation never remove
or clear them. Selection refusal before reservation creates no directory/intent/
action. Refusal after reservation retains uncertainty. Metadata bookends are
observations, not a hostile shared-user racing-rename fence.

Command-result and independently read remote-observation records stay separate.
Remote observation must match exact repository/PR/URL/H, MERGED, full lowercase
actual SHA1 and timezone-aware merge/observation times, with exact command and
selection digests. A failed/lost command, including command exit0 with lost remote
confirmation, remains uncertain even if a later separate read sees MERGED.
Neither parsing nor local disposition reduction creates a capability witness,
native callback, settlement, recovery permission or second action.

**U5b2b remaining:** extract/consume the common LOCAL CLI finalization action
core while preserving historical command/selection/output interpretation; wire
actual selected-head raw check/run/status observations; persist before-effect
intent; re-read live selection/current authority and immutable local repository
subject before the sole locally called expected-head non-admin guarded merge;
retain original command failure and independent remote observation; validate the
genuine callback observation through compatible complete/selected history readers
and the existing rollback-then-business-Held/cancellation owners. No producer may
mount ahead of that reader/authority/cancellation boundary. Existing historical
`workflow_coding_finalize.py` and `pr_ci_merge` are byte-unchanged in U5b2a; only
the local journal imports their existing exclusive-write/directory helpers.

**Final success remains Held.** U5b2a deliberately supplies no action producer,
public Final selection/command, callback witness or dispatcher mount. Additionally
U6 must close mixed history and U7 must supply genuine task-session transport;
a local observation cannot bypass either or replace native terminal/positive
outer settlement. Therefore `guarded-repository-merge@1` retains the existing
unavailable-output hold. No admitted success, real merge, native/V3 acceptance,
live installation/configuration or deployment is claimed. ACCESS-1 and the
shared-OS-user/shared-gh-credential limitation remain controlling.

| Added owner | Consumers and preservation boundary |
| --- | --- |
| `sequential_coding_final`: `_require`, `_native_pause`, `_live_owner`, `select_final_uncommitted`, `SequentialCodingFinalSelection.select` | Unmounted internal worker-thread service; consumes original dispatcher/Pause/tracker, authority and U5a reader/policy; no OrgState/route/CLI/config registration. |
| `sequential_coding_final_records`: closed models, canonical readers, `record_digest`, `final_disposition` | Consumed by that service and the unmounted local journal; syntax/reduction only, no callback/output witness or storage migration. |
| `workflow_sequential_final_journal`: `_current`, `attempt_directory`, `_sync_directory`, `FinalAttempt`, `reserve_attempt` | No production caller; future U5b2b selected-manager adapter must provide genuine fresh selection. Reuses existing local historical helpers without altering historical interpretation. |

Disposable diagnostics invoke the actual new service/U5a reader/policy using
explicitly synthetic authority, native live owner/request/handle/Pause and prior
callback/host/outer scaffolding. They exercise policy/refusal, same-boundary full
evidence, original-task journal variants and strict record readers. A controlled
fake guarded-merge adapter exercises command loss; no real gh merge is called.
This is source-interface diagnostic evidence only. Actual local raw remote
observations, mounted selection/CLI/action/callback, native cancellation races,
U6/U7 history/transport and real merge remain NOT RUN/deferred to their owners.
