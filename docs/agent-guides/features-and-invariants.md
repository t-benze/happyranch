# Features And Invariants

## Approved online team move — C1 task/host and C2 thread source (THR296seq135)

B supplies private durable storage; C1 connects holds to task allocation/claim,
preparation and final Pause/host commitment. Causal task/workflow closure and
actual retry/callback/cleanup ownership participate; unknown quiescence stays
reserved. C3/D producer/recovery/startup/old-reader closure remains absent.
Same-key/body replay is read-only; changed body/active conflict/stale CAS refuse.
Precommit refusal may release empty progress; postcommit repair retains its hold
without TTL. No public commands or COMPLETE writer exist until full C2-D closure and
actual canonical/context/reset/ready-authority validation. Manage-agent cannot
transfer teams. Preserve histories/IDs/paths/permissions and Engineering routing.
History validation shares the writer's finite forward/repair edges, requires the
initial empty WAITING reservation and phase-local decision/receipt evidence,
and forbids terminal resurrection. Repair returns only to its interrupted phase;
retained recovery ownership remains required. Unchanged replay adds no audit.
Move admission deferral propagates through the inner launch handler to the existing
pending-work owner without task failure, parent wake, followup or callback consumption.
Malformed installed state retains its actionable admission error. Direct 429 and
supervised passthrough retries preserve each returned uncertain attempt as a separate
Pause journal/reservation blocker; the next actual attempt keeps the original retry
budget, session and enqueue age and rechecks durable admission before FIFO/commit.
Provider return never proves containment. Durable Pause attribution remains through
workflow reconciliation and the actual outer cleanup batch. Its explicit disposition
distinguishes safe no-action/preservation from halted, unreaped, action/report failure
or unknown teardown; uncertainty retains the exact task/session owner and cleanup
facts, without changing the normal terminal result or expanding cleanup authority.
Retry attribution uses the existing entry id, task/agent/SID, owner identity,
fingerprint, generation and retry fields; the retained reservation adopts that
entry id only after journal COMMIT. The closed Pause journal format is unchanged.
Failed-tail diagnostics append to existing `cleanup.suffix_lines` within its
65536-byte UTF8 allowance, preserving earlier lines; excess new diagnostics are
explicitly truncated and logged. `execution_unknown` retains ownership independently
of diagnostic capacity. These lines are diagnostics, never quiescence proof or
permission to replay an unknown entry. A refused save retains failed ownership
and propagates unless an original exception is already unwinding; then that
original exception carries the settlement failure note and log. Every collected
outer-tail owner is visited even if another settlement fails.

C2 now reserves trusted thread/participant/INTEGER invocation/SID ownership before
preparation, then composes final claims and launch with the short gate, DB and
host terminal ordering. All purposes and actual host/direct429, eviction and
bounded-nudge paths participate. The existing thread audit owner stores strict
bounded append-only attempt/deferral evidence; no schema or task-Pause format
changes. Unknown prior retries stay blockers after later clean outcomes.
Captured actual running turns alone may settle under WAITING; no drain label
admits new work. Queue dedup spans queued/in-flight work, typed deferrals release
notification ownership, and complete durable runnable discovery/redelivery keeps
pending tokens/ranges/probe/catchup obligations intact. Native thread recovery
and raw reapers preserve held/C2-owned history. Participant/session writes
cooperate during future forward reset; messages remain admissible. The bounded
thread census/drain and post-release seams are available to D, whose complete
startup/containment/operation/old-reader closure remains absent. No public apply
or COMPLETE writer; APPLY_AVAILABLE stays false. Behavior remains UNEXECUTED,
integration SKIPPED24342; no new product tests/fixtures or activation authority.
Native archive admission precedes delivery discard, BOOTSTRAP reaping, status,
participant resets, their audits and the archive system message in one DB-owned
transaction serialized against a forward decision. Refusal leaves all those
obligations and history unchanged; transcript files/events follow COMMIT.
Authentic provider eviction audit and compare-and-clear of the attempted stale
SID commit before fallback phase selection/admission. Only the actual live
producer's durable launched/settled initial attempt can record that observation
under a winning hold; callback/drain/new-launch and general participant guards
remain unchanged. Watermarks, delivery ranges/tokens and runtime SID are retained;
unknown cleanup remains a blocker. Deferred re-entry cannot reuse the evicted SID.
These archive/eviction/re-entry behavioral observations remain UNEXECUTED.

See the [approved contract](../superpowers/specs/2026-10-14-online-team-move.md).
Behavior/recovery remains UNEXECUTED; source/static acceptance is separate.



**Mounted generic owner review — THR139465/480/493/506, ROOT TASK10693.**
The normal `/orgs/:slug/reviews` list and `/orgs/:slug/reviews/:activationId`
detail use owner-selected discovery and the existing recipient-safe reads.
Template pins supply output/role/outcome labels; there are no template-name
branches. Exact selected identity, canonical base64, strict UTF8 and SHA256 are
validated before rendering raw text. Current r1/r2 and read-only History remain
separate; an individual recorded response does not establish joined approval.
Only current collection validity with approved_use establishes approved input.
Explicit Request changes or Sign off requires exact nonempty operator findings
and a matching @1/@2/@3 command. A synchronous latch precedes hashing; one
closed key/body is frozen in sessionStorage before POST. Ambiguous sends lock
editing/opposite effects and offer only explicit same-command replay after a
fresh selected read, including reload. No effect/poll/locale change auto-sends.
Current read/refetch errors gate effects; access refusal hides selected bytes,
drafts and receipts and evicts sensitive selected cache/envelopes. Query identity
includes org/activation/lane/object; outgoing reads abort, panes detach, and late
hash/mutation results cannot affect a new selection. Locale changes preserve
mounted drafts/focus/selection without network work.
Return to author is a separate confirmation following full server readiness;
POST independently rechecks the original r1 CAS and authority. Genuine successor
and new author task IDs/status come from the server, with no optimistic r2 bytes
or receipt. Fresh r2 responses are required; further revision is unsupported.
Frozen original owner-return replay uses current access, identity and authority
admission separately from new-effect readiness. A pending genuine successor's
held/noncurrent collection permits only explicit identical acknowledgement
recovery; it never permits a new command. Current graph/status comes from the
selected successor independently of retained r1. Pending author completion does
not mean r2 reviews closed; unsupported further revision appears only for a
current objected r2 graph. Selected-read start/refusal or snapshot change
invalidates the admission token synchronously; the writer rechecks that token
and selected identity immediately after asynchronous command validation.
No raw proof editor, peer cancellation, author staging, direct submission,
r3/reassignment, execution/merge or digest-derived access controls are mounted.
This frontend source requires the compatible discovery/revision readers; old
daemon errors remain visible dependencies. No backend/native/schema/auth/shared
transport/dependency/hook changes follow. Bounded intercepted frontend rendering
and assertions establish UI behavior only; independent final-head review/scoped
QA and the assigned public synthetic two-template journey remain separate gates.
Real Founder Request changes AND Sign off and genuine agent judgment remain
UNOBSERVED/root-owned. Python units RETIRED; integration/callback SKIPPED24342;
broad/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Required automated
CI/hooks remain Source Boundary Lint + Web Lint(Node24), static only.


**Document revision C1 — Founder THR139seq493/490, ROOT TASK10693.**
The same current Founder owner explicitly returns a fully collected, objected
r1 to the same author with a pinned CAS command. Pending peers survive objections.
POST `/api/v1/orgs/{slug}/workflows/instances/{instance_id}/revisions` takes
`workflow-document-revision-command@1`: operation_key, instance_id, activation_id,
expected_revision=1, predecessor_submission_id/digest, predecessor_round_id and
original authority pin. It returns201 or exact replay200 without another task.
GET `/workflows/revisions/{revision_operation_id}` selects exactly owner-human
bearer or the new-author current session; its closed receipt separates retained
identity/history from current eligibility and exposes no claim/host controls.
The new genuine task owns a new context/intent with admission_kind=revision,
attempt/generation2 and unchanged activation1, owner/binding/authorization/root.
POST `/workflows/revisions/{revision_operation_id}/submissions?session_id=CURRENT`
stages `workflow-author-revision-command@1` with literal2 and artifact_kind=document.
CLI `workflows revisions start/show/stage` matches these transports; stage requires
loopback session proof, without bearer fallback. Browser exposes owner/read only.
Staged G bytes keep permanent NULL legacy result; only the new task's own full
INTEGER callback and successful Boolean-quiescent terminal append the genuine
result link and fresh configured review requests/agent tasks. Review scope,
human/stage commands, receipts, acknowledgement and service join select explicit
@3/r2; old @1/@2 remain literal r1 and byte-compatible. Each current reviewer gets
only its own bounded r1 prior_review. The author gets all closed findings and
only originally addressed inputs. Fresh all-slot approvals yield one service
join; old receipts and tasks never approve r2. Historical r1 is needs_revision
with reason noncurrent_successor after return; current assignment replacement
retains all historical tasks/results/evidence/contributors and receipts.
Layout I changes only the draft admission enum and adds ONE
workflow_document_revision_operations table. F/E/G/H independent readers remain;
H literal r1/unique intent and every old schema/data byte are preserved. Proven
fresh org creation initializes I. Existing startup/reopen/enable only validates.
Explicit owned idle H→I operator source is
`scripts/migrate_workflow_document_revision_schema.py --org-root ABSOLUTE_ORG_ROOT
--org-slug ORG [--check]`. No live migration was executed. Even empty I needs a
compatible reader; no old-reader downgrade claim. Native denies the exact new
author POST using independent raw/normalized one-pass candidates before credentials;
shared normalizer/transport/auth/host permissions remain unchanged.
C1 source is delivered; wider recovery/reassignment/owner transfer/r3/direct-submit,
B2 repository resolution and mounted end-user UI remain separate. Code activation
still refuses409 before allocation. All new behavior/assertion/red-green/mutation/
repetition/proof/executable-QA is UNEXECUTED/FOUNDER-WAIVED139429/430; integration
SKIPPED24342; Python units RETIRED. Retained source assertions have not executed.
Source checks establish no behavior or migration safety. Independent full review,
same-head scoped QA, lint/static gates, normal hooks and manager guarded merge
remain. Founder465 public two-template/revision smoke and actual Founder UI
Request changes AND Sign off remain root-owned UNOBSERVED. This current C1
section supersedes historical C1-pending/fresh-H checkpoint descriptions below.


**Current reviewer staging — Founder THR139seq493/475, ROOT TASK10693.**
POST `/api/v1/orgs/{slug}/workflows/reviews/stages?session_id=CURRENT` and
`happyranch workflows reviews stage --org ORG --session-id CURRENT
--from-file /absolute/command.json [--json]` expose the existing current
non-recovery agent reviewer writer. Selected immutable request scope chooses
`workflow-review-stage-command@1`, `@2` or revision2 `@3`; old families remain closed and byte-compatible.
The five-field @1/@2/@3 acknowledgement (format, stage_event_id, request_digest,
replayed, signed=false) is unsigned. Genuine INTEGER/full callback and successful
Boolean-quiescent terminal under current authority remain necessary for a receipt.
The actual launch supplement carries selected request/digest/revision/outcomes
and staging-before-final-callback instructions; forthcoming invocation identity
is not session admission proof. CLI uses loopback session transport without
bearer fallback or automatic retry. This POST is browser-excluded and denied
natively before credential lookup/injection/forwarding. The private stage-only
comparison independently checks raw and normalized paths: literal query split,
one percent-decode with UTF8 replacement (malformed triplets and plus literal),
one final slash removal, existing prefix strip, exact family. Shared transport,
normalizer, H/G/r1/history and receipt validators retain their contracts.
This supersedes the historical reviewer-stage deferrals below; direct-submit,
B2 repository access, wider recovery/UI and Founder465
public two-template/revision smoke plus real Request changes AND Sign off remain
separate/root-owned. New behavior/proof is UNEXECUTED/FOUNDER-WAIVED139429/430;
integration SKIPPED24342, Python units RETIRED; automation is lint/static only.


**Matched artifact admission B1 — THR139seq480/477, ROOT TASK10693.**
A closed `artifact-review` definition with strict schema_version 4 compiles to
`workflow-artifact-contract@4`; exact pins are workflow-compiler@4,
workflow-validator@4 and operator-input@4. The template selects exactly one
accepted kind in output `{primitive:"immutable-artifact-revision",description,
accepted_kinds:["document"]}` or `["code-change"]`. Role names/kinds/counts,
outcomes, approval, return rules and submission timing remain finite template
data, independently bound to actual current principals. No name/content sniffing
or new product-design branch selects policy. Submission delivery retains the
literal current-author-handoff@1 declaration; B1 implements original-author r1.

Matched activation-request/receipt/authorization/binding/initial-draft-context@4
uses draft-artifact, stage-immutable-artifact, submit-immutable-artifact and the
existing collect-review/approve-planning-input/return-to-author semantics.
These grant preparation/review-input only. Context stores artifact_contract;
request requires repository_access; for documents it is null. Authorization,
binding and context require repository_binding null. A code definition may be
stored, but valid code activation returns409 workflow_repository_reader_unavailable
before instance/task/intent allocation. No code submission, repository reading,
access grant/capsule, execution, merge or delivered-code claim is implemented.

The document r1 chain uses author-handoff-command/request/proof/receipt@2.
Command adds artifact_kind document; proof adds repository_resolution null;
public core adds artifact_kind document. The source supplement gives the actual
@2 command and stable original key, staged before the genuine final callback.
The new submission ID preimage uses workflow-author-handoff@2. The review scope
is workflow-review-request@2 with the explicit artifact identity (org namespace,
instance and output slot), submission ID, strict revision1, document kind, exact
bytes digest and content_digest subject. It adds repository_resolution null.
Human-review-command/proof/receipt, review-stage-command, agent-review-proof,
review-receipt, review-request-receipt and review-join use matched @2 families.
B1 added no stage endpoint; seq475 staging is described above. Selected-principal/grant checks
precede private decode and repeat after waits/in the writer. Both authentic
receipt lanes, INTEGER/full callback and successful Boolean-quiescent terminal,
contributor independence, retained objections and collect-all rules remain.
No object I/O occurs in the writer or historical/cold readers.

Existing @1/@2/@3 historical definitions/contexts and @1 document handoff,
scope/brief/proof/receipt/join interpretations remain byte-exact. Unknown/mixed
families refuse; no relabel, backfill or automatic repin occurs. F/E/G/H DDL,
digests, H17 columns/constraints/revision1/unique intent and marker meanings are
unchanged. Publication requires pre-existing H. Any durable @4 template,
context or authorization already requires a compatible reader before the first
H row; no old-reader downgrade or migration claim follows. Compatible-reader
rollback must retain the new history; deleting/converting it is not authorized.

Prospective frozen reservations (non-null use remains held B2): repository_access
is `{action:"read-repository-artifact",repository_id,recipients}` with ordered
roles author then all reviewers. Binding is `{format:"workflow-repository-binding@1",
action:"read-repository-artifact",org_slug,repository_id,repo_key,origin_digest,
store_owner,authority:{namespace,generation,snapshot_digest},
recipients:[{role,kind,principal,team}],reader:"local-git-objects@1"}`.
Resolution is `{binding,binding_digest,presentation_bytes_base64,presentation_digest}`;
presentation is `{format:"workflow-repository-presentation@1",repository_id,
object_format,head,base,entries:[{path,before,after}]}` with each side null or
`{mode,blob_oid,bytes_base64}`. B1 accepts/stores only null binding/resolution;
these declarations are grammar reservations, never access or object-resolution
proof. Incompatible future shapes need a fresh version.

Existing routes/CLI/TS/OpenAPI carry matched unions, without a new route,
audience, native permission or shared transport change. B2 repository access
and bounded immutable-object reader, C r2/fresh signoffs/direct submission/owner
validity/reassignment/recovery/mounted UI remain held or separate. Founder465
public two-template/revision journeys and actual Founder UI Request changes
and Sign off remain root-owned UNOBSERVED; Phase2 ordered coding/QA/CI/merge is
separate. Behavioral/assertion/red-green/mutation/repetition/proof is UNEXECUTED /
FOUNDER-WAIVED139429/430, never PASS; integration SKIPPED24342 and Python units
RETIRED. Automated checks are lint/static only; no ad hoc E2E/live operation.
Fresh full independent review/scoped QA binds the final actual pushed head.


**Retained artifact foundation A — THR139 seq480/477, ROOT TASK10693.**
`runtime/workflows/artifacts.py` supplies a pure frozen `ArtifactRevision`:
org namespace + workflow instance + stable output slot (`output` for the current
single-output document family), stored submission ID, positive strict-integer
revision, explicit document/code-change subject, and authenticated provenance
references/snapshots. Output descriptions and submission IDs are not stable
artifact identity. Source task/session, genuine optional INTEGER result link,
author/contributor records and pinned template/context/binding/authorization
references remain caller-owned evidence; an active author gets no invented
result and a human reviewer gets no invented task.

Document projections verify exact UTF8 bytes (1..1048576) and raw SHA256.
Submission integrity and both human/agent receipt subject checks consume this
projection. Existing `review_scope`/`review_brief` wrappers select the document
compatibility adapter: exact historical scope, purpose, brief (including its
historical later-receipts sentence), bytes, IDs, digest preimages and proof formats
stay unchanged. Selected recipient/grant checks still precede private decode;
after-wait/current-authority writer checks, contributor independence and genuine
callback/Boolean-quiescence proof stay in existing owners. The pure module has
no store/schema/graph import, I/O, registry or recursive validation.

Closed canonical `workflow-code-change@1` descriptors require kind code-change,
repository `{id}` (opaque binding Token), object_format sha1/sha256, full lowercase
40/64-character head, comparison null or `{base}` in that format, and navigation
`{branch,pr_url}` with nullable bounded UTF8 text (1..256/1..2048 bytes). All keys
are required; unknown/duplicate keys, symbolic/short OIDs and malformed types
refuse. Canonical descriptor bytes use the same 1MiB limit. Repository/algorithm/
head/base define the immutable object subject; descriptor SHA256 additionally
pins the navigation snapshot. No path/URL resolution, content sniffing, fetch,
checkout, execution or access grant follows from parsing. A syntactically valid
absent object can parse; no repository reader is mounted in A.

Product-design and the differently named one-human proposal use the same document
projection with template-defined roles/outcomes; code-change shares the identity
model as representation only. B1 adds the matched document admission described
above; B2 still owns actual authorized immutable repository resolution.
C still owns r2 producers/fresh requests/sign-offs, reassignment/recovery and
mounted UI/CLI. H revision1/unique-intent constraints and old @1/@2/@3 pins/history
remain intact. Seq493 approves the seq475 reviewer-stage source adapter and native denial
described above. Founder465 two-template public scratch/revision acceptance
and real Founder UI Request changes/Sign off remain UNOBSERVED. Ordered development/
review/QA, CI and guarded merge adapters/general coding/fork-join remain Phase2.
A is source-only delivery, not behavioral acceptance: behavior/proof campaigns
UNEXECUTED / FOUNDER-WAIVED139429/430; integration SKIPPED24342; Python units RETIRED.
Full independent review, scoped QA and exact-head lint renew before manager merge.


**U3b(d) human public write and selected signed read — same THR139 authority.**
POST `/api/v1/orgs/{slug}/workflows/reviews/human-receipts` mounts ONLY the
existing `admit_human` guarded writer through the server-derived Founder-bearer
principal. The retained closed HumanReviewCommand selects one request; no query
proof, body actor, task/session/result or agent lane is accepted. HTTP200 returns
the original immutable receipt on an exact actor/key/full-payload retry;
changed payload conflicts409. Peers/findings and all-current collection remain
owned by the existing writer. Generic pinned kinds/outcomes/recipients and complete
contributor independence govern. The explicit `workflow-review-receipt@1` response
contains only receipt/request/submission identity and digests, generation,
outcome, original timestamp and that recipient's findings encoding/digest.
Private proof, admission/pins, unrelated inputs, peer findings and credentials
are excluded.

Existing request GET admits pending and genuine signed rows with the SAME
selected human or actual CURRENT reviewer-session predicates. Current grant
selection still precedes private H storage; binding leases, authority writer,
after-wait rechecks and full receipt/graph validation remain. Historical proof
never keeps an expired session or grants owner access. Response status is the
real request row: pending/signed=false/pending=true/receipt=null, or approved or
changes_requested/signed=true/pending=false/receipt=the safe projection. Stored
scope/brief bytes, including the historical unsigned-purpose literal, are unchanged.
Server served models/OpenAPI, CLI and shared TS agree. Reviewer staging/native denial are described above; direct-submit
remains deferred and client.ts is unchanged. No owner-resolution/revision/reassignment writer or new
owner-read audience. Behavioral/proof results are UNEXECUTED/FOUNDER-WAIVED429/430;
root-owned real two-template public smoke465 remains required before invitation.
The draft stays dependency-incomplete/unaccepted/unmerged pending manager assessment,
full cumulative independent review, honest same-head QA and guarded merge.

**U3b(a) internal agent checkpoint — THR139 D5/G395/generic410/H445/457/465.**
`WorkflowReviewDispatcher.stage_review` is an internal operation; no public or
native stage surface is mounted. Its closed `workflow-review-stage-command@1`
requires action `stage-review`, operation/request tokens using the existing
1..128 `[A-Za-z0-9._:-]` grammar, strict integer expected_revision=1, the exact
request-scope digest, a template-declared outcome, canonical base64 UTF8 findings
of 1..1048576 bytes (encoded field 4..1398104 characters), and raw SHA256.
The existing sequenced F5 event stores `workflow-review-stage@1`: the closed
lifecycle envelope plus `stage={command,request_digest,scope,outbox_id,session_id}`.
The digest covers the full canonical command; scope and before/after projections
bind the actual selected reviewer/session, subject, revision, request, role kind,
assignment generation, context, binding, template and authority pins. One stage
per current request is immutable; org/principal/operation-key reuse across requests
conflicts. Eligible current-session exact full-body replay
returns its original event, and a changed body/key conflicts. Before and after
are identical, result and terminal evidence are null, and the acknowledgment
counts zero signatures. Cancelled, stale, nonrecipient and contributor actors,
undeclared outcomes and Boolean/integer substitutions refuse before effects.

`WorkflowReviewReceiptStore.admit_completed` consumes that stage only after the
exact actual INTEGER/full callback and committed successful/quiescent F5 terminal,
current native task/session assignment, source/activation/grant/authority and
instance-wide contributor independence revalidate. It atomically appends the
real completed instance-task/result/current-assignment/receipt/evidence bridge
and updates that request's outcome, then consumes all-current collection in the same writer. All proof bytes are reconstructed by
matched closed graph readers. Callback verdict alone cannot sign; absent stages,
failed/blocked/cancelled/uncertain evidence or manager effects remain unsigned.
The native terminal hook and existing lifespan/reaper discovery consume committed
results without another scheduler, host launch or PID/TTL inference. Roles and
outcomes come from pinned template data, including agent-looking human labels.

U3b(d) above mounts human write and genuine selected signed GET. Reviewer staging/native denial are described above;
direct-submit remains deferred. Root-owned
two-template public scratch smoke465 remains a dependency. Collection below is internal backend
work; keep the draft unaccepted/unmerged.

**U3b(b) internal human receipt checkpoint — same THR139 authority.**
`WorkflowReviewReceiptStore.admit_human` takes an existing server-derived
`WorkflowTemplatePrincipal` separately from the body. It requires the actual
human Founder/founder_bearer lane, no task/session, and revalidates before scope
selection, after awaited locks, inside the existing authority writer, and before
commit. Selected recipient and current grant admission precede full private H
decode; source validity, enabled cutover, current activation/authority/actions,
complete historical closure and whole-instance contributor independence must
still hold. Pinned role **kind**, output and allowed outcomes select behavior;
role labels, product names and reviewer counts supply no human authority.

The closed `workflow-human-review-command@1` has action `sign-review`,
operation_key/request_id Token `[A-Za-z0-9._:-]{1,128}`, strict integer
expected_revision=1, exact request_scope_digest, outcome `approved` or
`changes_requested` additionally declared by the template, canonical base64
findings_bytes_base64 (4..1398104 characters, 1..1048576 decoded UTF8 bytes), and
findings_digest (SHA256 of the exact raw bytes). Canonical command <=2097152
bytes. No body actor, proof Boolean, credentials or synthetic task/result.

`workflow-human-review-proof@1` is a closed pure-data object with exactly
`format,actor,operation_principal,command,request_digest,scope,request_scope_digest,
pins,admission_authority,created_at`. Actor is exactly
`{principal_kind:human,principal_id:founder,proof_kind:founder_bearer}` and
operation_principal is `human:founder`. Request digest covers the **full canonical
command**, including operation key and raw findings encoding/digest. Scope binds
actual subject/revision/raw artifact digest, request, role/principal/generation
and all recipient inputs. Pins retain the complete H template, activation,
authorization/grant, binding, context, source scope and generic contract bytes.
Admission authority retains the validated full H ready/pointer/profiles/mirror/
enabled-cutover closure with actions exactly `[collect-review]`; current admission
also compares the complete fresh capture inside the writer. Created_at is UTC
`YYYY-MM-DDTHH:MM:SS.ffffffZ`; canonical proof <=16777216 bytes. The reader derives
and compares the entire canonical proof and receipt row with strict JSON types,
command/time/byte bounds and authentic saved closure. Boolean/integer substitutions,
extra fields, partial or mismatched proof refuse. Historical validation invokes
no bearer callback and supplies no current mutation authority.

One org/actual human/operation-key identifies the immutable full payload. After
CURRENT admission, exact replay returns the original receipt bytes/ID/timestamp;
changed body/key or cross-request key reuse conflicts with zero partial effects.
The guarded transaction inserts the human receipt, updates its selected outcome
and consumes all-current collection below. No human task/session/result/current
assignment, agent receipt_evidence bridge, F5 stage or host-terminal proof exists.
Peers/findings remain immutable. Matched readers accept pending and signed rows.

U3b(d) above is the human public caller and selected pending/signed read adapter.
Reviewer staging/native denial are described above; direct-submit remains deferred.
All human behavior/proof execution remains UNEXECUTED; retained agent source
is not accepted proof. Root-owned real two-template public scratch smoke465 is
required before Founder invitation and remains separate from daily Product E2E.

**U3b(c) collect-all/service join — same authority.** Both receipt writers invoke
`review_join.collect_in_writer` within the existing authority writer. Only ALL
configured current slots responding closes collection. An objection closes the
round while the instance stays reviewing and derives needs_revision/owner action;
no joined event, new status enum, automatic r2 or timeout approval. Cutover keeps
closed objection rounds blocked for U4 owner revision. All approvals revalidate
current collect-review/approve-planning-input grant, activation, full captured
ready/pointer/profile/mirror closure, enabled cutover, artifact, recipient/
generation, contributor independence and every genuine receipt after waits.
The instance and round complete atomically with one service event/replay.
Pinned template roles/kinds/count/outcomes/all-current approval select behavior;
product-design and differently named one-human proposal share the mechanism.
Author contributes no signature. Existing H fulfillment still requires the
original successful callback/quiescent terminal; this adds no author-completion
gate and does not implement/prove the deferred direct-active-submit producer.

Closed canonical `workflow-review-join@1` keys are exactly
`format,event_kind,org_slug,instance_id,round_id,submission_id,revision,
submission_digest,operation,pins,admission_authority,receipts,assignments,created_at`.
Event kind is joined; revision is strict integer1; submission_digest hashes raw
immutable UTF8. Operation is exactly `{principal:service:workflow_review_join,
operation_key:<actual round ID>,action:approve-planning-input}`. Pins are the full
seven H pins (including immutable grant/binding/context bytes); admission_authority
is the full saved H ready/pointer/profiles/mirror/enabled-cutover closure with
actions `[collect-review,approve-planning-input]`, freshly compared in the writer.
Receipts in declared role order contain entire immutable rows, with bytes encoded
as lowercase hex; assignments in declared agent-role order contain whole actual
current assignment rows. Receipt readers bind exact INTEGER/full successful
callbacks, genuine task/session/result/generation and Boolean host quiescence;
human proof binds real bearer provenance without synthetic task/result.
Created_at is UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Event ID is deterministic per
instance/revision/joined and event_digest is raw canonical SHA256. The existing
operation replay contains exact org/service/round-key, raw event request_digest,
instance and effect ID. Full reconstruction authenticates the complete event and
replay; extra/malformed/type-substituted fields refuse. Historical validation
runs after receipt validation, never recursively through schema/graph/current
admission, and preserves earlier event interpretations.

Exact retry and human/agent/concurrent last response use the same writer and IDs.
Existing lifespan/reaper discovery handles a committed-last-receipt gap without
another scheduler; whitelisted ordinary authority holds continue to later work,
while corruption propagates. Closed joins with a later relational source or
authorization/activation/authority-pointer hold also block drain for owner
reconciliation. Internal collection current validity/approved-use
reports source/authority holds and owner reconciliation independently of retained
joined history. A historical join cannot authorize fresh use; explicit durable
owner resolution/revision/reassignment remains U4. All lifecycle/proof cases are
source-inspected only, UNEXECUTED/FOUNDER-WAIVED139429/430. Deferred public-stage disposition and root
smoke465 remain dependencies; smoke465 is required before Founder invitation.

> Test-policy update (THR-291 seq40): former Python unit tests and unit-only
> fixtures referenced in historical implementation evidence below are retired.
> Product contracts are unchanged; replacement E2E is PENDING. See
> [coverage gaps](../python-test-reset.md) and [current lanes](../local-ci.md).

## Current CLI/prompt naming invariants

TASK10385 adds operator identity inspect/resolve/rename with name-revision CAS
and uncertain-failure readback. Human destinations/filter labels resolve to IDs;
former names report current names without forwarding. Founder transport stays
`@founder`, never agent participation/ownership. Historical IDs and unavailable-
naming ID operations preserve original owner checks. Human Name · ID output is
explicitly current enrichment, not a historical snapshot or JSON/history rewrite.


New-message body admission excludes email/word interiors, escaped tokens and
supported inline/fenced code; ordinary quoted prose remains eligible. Literal-only
bodies perform no body naming lookup. Genuine body aliases fail closed on service
failure, including empty explicit recipients and per-token unavailability. Known
actual canonical IDs retain owner checks; ID-shaped aliases supply no evidence.
Observed Former refusal precedes later chunk failures. en/zh-CN service wording
asks for retry or a known intended-recipient ID, with no body rewrite, automatic
retry/send or locale-triggered effect. Historical messages/delivery and release
measurement keep their original interpretation. The exact finite scanner contract
is in the current identity-names core spec (THR293seq174).

All associated CLI upload paths preflight former body/recipient tokens; final
forwarded text is checked using the same explicit-prose scanner as new message admission.
Unknown text remains intact. A read preflight is no reservation and uploads are
independent; server action admission can refuse after an intervening rename.
Current task/full/resumed/fallback/dream/wake/schedule context refreshes labels
without changing canonical role/authority/session/queue/proof bindings. Founder
is separate human context; metadata failure supplies ID-only presentation.

Relevant source assertions are inside scenarios5/8/9. The twelve-scenario set
has source-pinned maker E2E receipts; old233/297 material remains historical.
Actual provider resume/fallback launches, auxiliary runner launches and
rename-after-upload races remain outside the finite proof. Real browser/picker
receipts are distinct from frontend mocks. UI/types and the naming runner are
published on the open PR; independent full review/QA and guarded merge remain
required. See the [current naming contract](../superpowers/specs/2026-10-09-identity-names-core.md).

## Naming v1 core, API and routing source

The internal naming registry retains chosen/current/former ownership permanently,
while default-only rejected reservations release. Absent-owner revision metadata
survives, preventing ABA; exact same-ID reenrollment restores chosen spelling.
Former classification is a diagnostic and does not forward to a current recipient.
Founder has typed human ownership. Naming projection does not infer bootstrap,
archive restoration or termination-cleanup success from canonical coherence.

Current file-backed active/pending/terminated owners plus founder are enumerated
by the bounded naming API. Absent/history-only retained owners stay out of that
roster. Resolver distinguishes resolved, former_name, unknown_identity,
invalid_identity_address, ineligible_identity and naming_unavailable. Former
claims yield only a typed diagnostic/current name; eligible=false prevents
forwarding. Founder remains human and cannot be a task owner or agent participant.
Reads never install/reconcile and preserve baseline canonical-ID availability
with explicit null label/revision metadata on naming-only failure. The routing parser retains ordinary unknown @text behavior.

The bounded naming diagnostic retains the last actual failed install category
when a later non-install refresh only observes absent naming. Absence without
that provenance remains `naming_not_installed`; stale diagnostics are not installer
evidence. A different current failure takes precedence while observed. A new
install attempt supersedes the cause, and a successful refresh clears it. This
volatile diagnostic changes no readiness/admission/write decision or public
response vocabulary and adds no retry or read-time reconciliation.


Current routing source resolves names/IDs case-insensitively with the unchanged
@token grammar. Former inputs refuse409 with current_name before thread creation,
stale closure, callback settlement or application attachment finalization. A
lookup grants no reservation; action boundaries revalidate. Actors/proofs remain
ID-only. Current-founder-only messages suppress new broadcast/cohort wakes, retain
full-recipient obligations and enqueue prior catch-up separately. Founder remains
human inbox only. Restart uses durable IDs/ranges/tokens/exchange/deferral rows;
no historical body reclassification or schema addition occurs. Filesystem capture
precedes the RLock and multipart reads precede final no-await admission/write.
Unavailable naming keeps actual ID behavior; non-ID addresses refuse503.

THR293seq34/35 requires twelve readable E2E scenarios. Core/API/routing,
CLI/prompt and UI/type source are present in the combined candidate. The existing
routing bodies and CLI assertions remain reusable E2E material; shipping-page
MSW assertions cover frontend portions of scenarios2/3/4/5/8/10, without daemon/DB
proof. Exact execution results and remaining obligations live in the maker
handoff. Python units/collection/duration are RETIRED under THR-291 seq40 and
general integration is SKIPPED THR243seq42, never PASS. Focused naming E2E requires
a supported disposable GitHub runner or Mac Linux guest with finite commands,
resources, output, deadline and actual cleanup. Independent full review,
behavioral QA, applicable checks, normal publication and guarded merge remain.


[Current contract](../superpowers/specs/2026-10-09-identity-names-core.md).


**H handoff storage — U3a(a), THR139 seq445/465.** Proven-fresh org creation
initializes H (complete G plus the exact `workflow_author_handoffs` table: 17
columns, six automatic constraint indexes, no marker or explicit index).
Existing F/E/G startup, reopen and enable validate their installed layout and
never add H. F/E must use the separately approved G migration first. The
explicit offline org-only command is
`python scripts/migrate_workflow_author_handoff_schema.py --org-root <absolute-org-root> --org-slug <slug> [--check]`.
For this H command, `--check` returns 1 for valid G requiring migration, 0 for
validated H; ordinary migration/repeat returns 0, refusal 1, parser error 2.
One short writer rechecks source ownership and complete independent references,
adds only the approved table, validates the target and retained rows, then
commits; failures roll back. Operator stop/ownership cooperation is required;
the script stops no process and cannot exclude an arbitrary concurrent start.
Even empty H requires an H-compatible reader. F/E/G SQL, IDs, history, permanent
NULL active legacy result and genuine INTEGER result links retain their meaning.
Whole-database F/E/G/H references preserve the independent Pause/Resume dimension;
generic Database/runtime-audit remain workflow-free and authority-v2 observed-only.

Complete H validation authenticates historical profile readiness from the saved
full dependency/store/registry mirror. Every closed variant preserves raw
nonempty source profile names; namespace and consumer identities are nonblank
UTF8 strings of 1..256 bytes without NUL, generations are exact integers in
0..9223372036854775807, and digests are full lowercase SHA256 strings. Native
row grouping/order and unrelated or removed rows remain intact. The historical
org's active requirements must have matching active store and registry
generations, and their full names, digests and ordered consumer membership must
reconstruct the pinned canonical profile projection. Empty mirror with required
profiles, missing/mismatched required rows or unbound requirements refuse
`workflow_author_handoff_storage_corrupt` before graph creation; an empty
required projection remains valid. Readiness, the offline script and producers
share this reader. Admitted GET exposes only category500; invisible scope404 and
selected-grant403 retain earlier precedence. Today's mutable mirror is never
historical evidence; a genuine later authority change remains a current hold.

New H structured witnesses compare canonical JSON bytes against authentic
retained or derived projections, including the row, immutable source intent,
activation/template and byte pins, context, snapshot, saved pointer and profile
closure. Boolean true/false cannot substitute for integer 1/0 in any such
witness. Exact integers and legitimate embedded snapshot booleans retain their
types; historical event numeric semantics remain unchanged. A mismatch refuses
`workflow_author_handoff_storage_corrupt` before graph effects, with the same
selected scope/grant precedence.

New F5 review-dispatch events also preserve types across every before/after
projection, immutable transition, adjacent event and final retained row. Each
intermediate projection anchors immutable fields to native rows and validates
mutable field types; the initial projection derives the producer's queued
values. Complete callback records and reused results retain native task_results
types, acceptance is exact integer 1, and host quiescence is genuine Boolean
true. Boolean/integer substitutions refuse before owned effects, using
`workflow_review_storage_corrupt` internally and the existing admitted H
category500 mapping. Authentic flags and historical S2 numeric semantics remain.

U3a(b) adds matched immutable `@3` template/activation/context readers,
`submissions.validate_handoff_graph` and the internal handoff/review producers.
Historical `@1`/`@2` families and pins retain their interpretation. An original
CURRENT author with stage+submit authority reserves immutable bytes only. SAME
operation fulfillment requires its genuine INTEGER/full callback and committed
successful/quiescent terminal proof, plus current authority and independence.
The atomic graph retains NULL legacy result, appends the authentic result link,
and creates one unsigned request per configured slot. Actual agent tasks alone
populate the ordered 0..3 reviewer-task IDs; roles, kinds, outcomes and timing
come from immutable template data. Inputs remain filtered to each recipient.

The review owner reuses the existing scheduler/native host lifecycle and
Pause/Resume final admission, publication, prompt reader and cancellation fences.
Completed-source discovery precedes workers and periodic enqueue hints; possible
launch without affirmative proof remains uncertain. No callback is a signature.
U3a(c) mounts the closed handoff POST/GET and unsigned selected-request GET,
with CLI/TypeScript/OpenAPI/native parity and existing F5 callback, replay,
cancellation and portability owner selection. This complete U3a composition remains
unaccepted pending renewed independent same-head assessment and current static gates.

Current Founder-owner handoff GET may read a superseded source, including a
configuration with zero human reviewers, only with current instance ownership,
the SAME active authorization pointer and collect-review grant, full immutable
closure and coherent current authority. Source/request-agent selection remains
strict; staging, consumption and review dispatch retain current-source fences.
After valid admission, corruption refuses500; failed/cancelled sources retain
their actual reconciliation flags. Otherwise projection precedence is disabled
(held/new_runs_disabled), superseded (held/source_not_current), changed coherent
authority (held/authority_not_current), then uncertain/missing proof and eligible
graph/callback states. Core stays identical; every held graph is hidden and GET
creates no domain, queue or launch effects. Callback-before-clear original-current
GET remains allowed; cleared sessions refuse through existing session admission.

Direct-submit remains deferred; reviewer staging/native denial are described above. Genuine
receipts/joins, revisions/reassignment/recovery, mounted operator UI and operator
acceptance remain later units. Full independent review, honest same-head QA,
unconditional exact-head Source Boundary Lint and Web Lint (Node 24), and
manager guarded merge remain required under THR291 seq69. Actual CLI callback
sources and isolation fences remain retained; execution is not a merge check.
Manager-owned bounded two-template public smoke must precede Founder invitation.
No live migration/enable/assignment/deploy/restart follows.

**G submission-schema migration (THR139 seq395; retained F/E→G path).**
The earlier fresh initializer selected G; U3a(a) now selects H under the H contract.
Existing F/E startup,
reopen and enable retain their installed layout; S2 remains available on E.
The explicit org-only operator command is
`python scripts/migrate_workflow_submission_schema.py --runtime-root <absolute-root> --org <slug> [--check]`.
Check returns migration-needed (3) for valid F/E, ready (0) for complete G,
refused (1) for invalid source/ownership, and parser errors return 2. Actual
migration atomically replaces only submissions/events and adds the approved
three tables/two explicit indexes. Event revisions come from an unambiguous
retained submission/round/event/replay closure; ambiguity refuses without
rewriting history. Every G database, including an empty one, needs a compatible
reader. The original F/E definitions and pristine-F downgrade contract remain.
The older draft script still upgrades F to E and reports a validated G as a
no-write G replay. Legacy authority comparison uses independent complete
F/E/G whole-database references; authority-v2 remains observed-only.

An authorized upgrade requires the daemon to be stopped with its configured
home/registration observable and source owners/hosts reconciled. The command
reads bounded existing PID/port/registry evidence and reserves one SQLite
writer; it stops no process and provides no exclusion against an arbitrary
concurrent daemon start. Operator cooperation is a precondition. Active-origin
submissions retain NULL legacy result identity; separate authenticated operation
and INTEGER ordinary-result links preserve the actual result evidence.
U3a(b) internal submission/review/link producers are staged in the stacked draft.
Genuine receipts/joins/finalization, later U3-U6 units and
independent operator acceptance, including real Founder UI actions, remain pending. This implementation work does not authorize live migration,
enablement, deployment or a Phase1-completion claim.


This file serves two purposes. The **Feature Modules Overview** below is an orientation map of the product's feature modules — each module is one short paragraph (what it does) plus a pointer to its authoritative spec or implementation. The per-surface sections after it are the original feature-specific traps, to be read only when touching the relevant surface; the overview points down to those sections where one exists rather than restating them.

For current behavior use implementation, tests, and the OpenAPI snapshot; `docs/agent-guides/` explains those sources. Prefer them over the design specs — `docs/superpowers/specs/` is append-only design history unless `docs/superpowers/specs/README.md` marks a spec `current`.

## Founder-managed Default (THR296)


Existing Agents/detail/enrollment, recipient selectors and Work Hours retain
ordinary consultant identities and Default membership. Work Hours distinguishes
roster loading and failure with Retry from a successful empty roster. Human
leadership is a localized label, never an executable option or synthetic null
row. Engineering/Product policy eligibility remains guarded; worker policy GET
and mutation denial require server evidence, not merely hidden controls.

The bounded roster utility preserves unrelated canonical literal text and
closed memory/history/thread delivery/breaker/frozen evidence. Completed replay
requires operation-specific audit/journal/pointer facts, including zero rows and
already-cleared rows; equality alone supplies no no-helper/no-write proof.
C6/C8/C9 source assertions and finite crash/localization selections remain
unexecuted under the existing Python/M holds.

Selected human failed-leaf recovery retains its original exceptional operation
after native drain exception or cancellation. Same-process same-result reentry
returns `recovery_required`, preserving its phase, jobs and control residue for
next-start discovery; it performs no new settlement, parent effect or success
bookkeeping. FAILED job rows alone do not prove native cleanup. Pending callers
join the original owner; a distinct result identity uses its own operation.

Human team leadership never adds an AgentDef or launch identity. Native
thread-session invalidation atomically commits each consultant's resume reset
and its audit, without changing delivery/breaker state, messages, memory or
history. Compensation retains safely cleared resumes and publishes a new
coherent authority generation; it never restores an old ready pointer.
Workflow profile closure and version-aware authority readers must agree before
readiness. Supported-version, malformed-principal/team/routing and current-capture
checks refuse before new graph writes. Schema1 historical admission contexts,
IDs and digests remain unchanged through schema2 publication and cold replay;
template-definition schema1 and its @1 pins remain separate from authority JSON.
Portability remains preflight/reconcile plus explicitly authorized
closed-copy/restore/reopen; no archive/export/import service is added. The
bounded utility requires stopped service, persistent external restart inhibition,
closed verified backups and exact current-file CAS. No live migration is authorized.

## Feature Modules Overview

### Workflow cutover prerequisite

`runtime/workflows/cutover.py` owns the existing version-1 lifecycle marker and
its contiguous org-bound event chain. Founder bearer requests commit the
request/admission fence before verification or drain; recovery checks the full
canonical layout/history, SQLite integrity/FKs and real work closure under each
short writer reservation. GET and downgrade preflight never advance. Historical
request replay reconstructs the original action/CAS before obsolete gates and
returns its identity separately from current pending/enabled/draining state.

Historical S1/G foundation evidence writes only generation1/event1 and
validates complete F/E/G; at that checkpoint only explicit POST /orgs fresh creation
initialized G automatically. Current H initialization/readers are described above. Existing F requires the operator script documented in
runtime-and-configuration.md; startup/reopen/enable never adds draft DDL. Cold
`OrgState.load` forwards actual org context and resumes committed requests only
on ready E/G, retaining earlier F history and actionable guidance. Existing templates,
authority and profiles may remain inert; pre-enable activation/dispatch/recovery
work blocks enabling. Drain never cancels or settles F5 or draft work: queued/prelaunch,
running, uncertain and incomplete closures project actual owners and deferred
U2D/U4/U5 actions. Terminal history remains. Downgrade requires pristine F initial
history and no workflow data, including template-only data. Every E/G requires a
compatible reader even with zero business rows; version1 alone is metadata. Historical v0/v1
source-pinned initializer/migration tests prove preservation at that boundary,
not whole-runtime conversion or old-binary gate compliance. S1 supplies draft storage/closure validation only. Activation/first
draft production, dispatch/recovery routing and operator acceptance remain later work.

### Initial workflow draft lifecycle (S2)

The S2 source consumes the S1 draft lane through `WorkflowActivationStore` and
`WorkflowDraftDispatcher`, attached by actual OrgState. Founder exact-version
admission creates one genuine bounded author task/root, authorization, immutable
binding/context, activation, intent and admitted event in one writer. Template
publication alone grants no assignment. Historical replay retains original IDs
and pins with no queue effects; current eligibility is a separate projection.

Draft completion requires the authentic accepted result and finalized contained
host quiescence. Session publication alone does not prove launch; possible launch
without exact acknowledgment stays uncertain and is never blindly requeued.
Cancellation commits a fence before containment. Startup, run_step, callback,
zombie TTL and portability route owned work before ordinary lifecycle effects;
malformed or dual closure requires reconciliation. Draft authors, even managers,
receive no generic decision/delegate/then/fanout/code authority. F5 producers and
U3 immutable submission/reviews, U4/U5 replacement operations, U6 mounted UI and
operator acceptance remain separate. Independent review/QA/CI and deployment are
not implied by source implementation. Earlier prerequisite descriptions above
refer to S1; S2 does not change its migration/readiness/drain meanings.

### Finite template-driven draft policy

Document-review schema2 permits one abstract agent author and 1–3 reviewers,
with at most one existing Founder human. Required roles equal the declared reviewer
list on the current revision. Approved is required; optional changes_requested
requires the exact return-to-author/new/all-prior-receipts rule, otherwise return
is null. Submission timing declares on-completion or while-active-or-completed
capability for later producers. Author allocation, recipient filtering and the
frozen context consume the compiled contract from the exact immutable version.
A request cannot add return-to-author to an approval-only template. Agent-only
review has no human alias; all actual principals retain canonical active membership
and independence requirements. Initial execution creates only an author draft.

Legacy schema1 uses its unchanged @1 reader/brief/envelopes. New definition and
activation families use @2 pins/envelopes; numerical publication version and the
current pointer never choose interpretation. Full validation visits all historical
drafts/versions. Any @2 data requires a compatible reader, even without instances;
older readers refuse. No DDL/history conversion or downgrade guarantee is added.

### Implemented boundaries for retained features

KB input validation, duplicate detection, author stamps, and deletion checks live
in `runtime/daemon/routes/kb.py` and `runtime/infrastructure/kb_store.py`; their
route/store tests exercise the accepted and rejected operations. Long-lived,
cross-agent relevance and preferring supersession are editorial guidance in task
and reflection skills, not a server semantic-admission guarantee.

KB tags are projected as strings: the current writer/API contract emits a list,
while a legacy scalar frontmatter value is one tag (never an iterable of
characters). Missing, empty, or unsupported tag shapes project as no tags. The
Knowledge index is `/orgs/:slug/kb`; detail selection is only the explicit
`/orgs/:slug/kb/:entrySlug/...` route segment, so an enclosing router wildcard
must never open a detail drawer on the index. Detail Retry invalidates the
active org-and-entry query so the same slug can recover without navigation.
Escape or outside dismissal clears the selected URL; late responses must not
reopen the drawer. The shared drawer currently has no close-button control.

KB search consumes the server's `hits[{slug,title,snippet,score}]` in ranked order.
The UI joins the existing unfiltered list summaries by slug for type, whole tags,
and update time; it never casts a hit to a full entry or invents missing metadata.
Cards show the hit title/snippet. Type and tag facets intersect after the join,
preserving rank. Pending input/search or metadata refresh hides stale results;
metadata errors or missing hit metadata show the existing recoverable Knowledge
error, never successful-empty. Retry refreshes search and list metadata. Clearing
search restores the list immediately, and old query settlements cannot replace
the current query. Below 640px, the feature-local filter rail stacks above the
list, stays full-width and scrolls within a 16rem height bound. Header actions
stack below the title, and the list and detail drawer remain contained at 390px.
Desktop retains the side rail and header arrangement; shared App/Drawer
primitives are unchanged.


Generic remote jobs remain dormant. `runtime/remote_jobs/` provides contextual
v1 models and canonical validation. `remote_job_schema.py` installs the six
runner/workspace/attempt/receipt/observation/frame domain tables, enrollment
challenges, certificate expiry, migration markers, and nullable local-job linkage.
The identity migration validates exact shapes and empty legacy runner graphs;
existing local-job semantics are preserved. Model/migration tests provide the
contract; no production runner authentication, transport, or execution is implied.

`task_scratch_reclamation.py` is invoked only by the bounded pre-agent
`run_step` hook. Its private `collect_revalidate_seal_consume_disposable` seam retains each
bounded E1/C1/E2/C2/seal/E3/C3/E4 collector admission, compares stable typed
lifecycle/session/PID-start and complete public/private coverage projections
(including bucket classification/accounting and dominance, but not timestamps), and
binds C3's canonical workspace/root/manifest/census projection to its
successfully sealed stack-local row. It returns `None` before action on changed,
malformed/private-identity/boot-mismatched, unavailable, deadline-exhausted, or
finite-cap-exhausted observations; an executor partial failure claims zero and
leaves the remainder for a fresh refusal. It neither activates cleanup nor excludes future
writers or hostile same-UID swaps. Its assertion
shapes do not establish lifecycle/liveness provenance; Git evidence and ambiguous
identity/device evidence refuse reclamation. POSIX pathname removal does not
guarantee survival of hostile same-UID replacement in the final check/syscall
window. The scheduler remains a trigger/marker producer only; this cleanup adds
no scheduler activation, legacy-backlog eligibility, or unbounded deletion.

`Database.select_workspace_cleanup_reclamation_candidates` is likewise a
read-only planning helper called only by the `run_step` hook. It first rejects supplied
claim-count inputs other than initial `0 -> 1` before admission or SQL, then
uses a fresh durable owner read to validate a valid supplied pair before the
bounded marker/history,
newer-owner, six-raw-candidate, graph, and exact-result reads. It exposes each
read admission to the pre-agent `run_step` hook and returns `None` on every malformed,
oversized, related-live, inconsistent, or unavailable SQL/decode observation. It neither reads config
nor imports or invokes the reclamation consumer, writes an audit, or grants an
action permit. The helper performs seven base SQL observations plus at most
five exact result reads; the hook's initial config read and five fresh
config/owner pairs make the complete admission model 23 reads. A fifth consumer
admission at read 23 is allowed; prospective read 24 refuses before another
load. These admissions bound later work rather than preempting an in-flight SQL
or OS operation, and the six raw rows are never refilled after age filtering.
Persisted ISO ordering accepts parser-valid aware forms but explicitly refuses
the parser-selected raw hour-24 field on every supported interpreter across
calendar and ISO-week extended/basic forms (including parser-supported
one-character separators). The narrow boundary check follows the standard
parser's one date/time boundary; it does not mistake an overlapping ISO-week
minute field for hour 24, while a runtime parser's next-day normalization still
cannot change selector ordering.

### Task-scratch report-only observations

`runtime/orchestrator/task_scratch_report.py` consumes the existing lifecycle/process and coverage
collectors after `_launch_agent_with_scratch` returns and `_run_agent` resets
scratch context. Ordinary settlement can follow this observation: a still
nonterminal task retains its scratch. The existing due `_tick_org` reaches the
same coordinator through `trigger_cleanup`, off the event loop and before the
unchanged atomic cleanup-task allocation block. No new scheduler, threshold,
cooldown, counter, history reset, receipt, API or UI is introduced.

The coordinator appends `task_scratch_report` to the existing audit log with the
actual candidate task ID. Read these rows through the existing audit reader or
`happyranch audit --org <slug> <task-id> --json`. Each append has a unique
observation ID, caller source and producer observation ID, timestamps, boot,
identity digest, provenance and freshness limits. `would_reclaim` means only a
report/no-op; actual reclaimed bytes and inodes are always zero. Missing or
partial measurements are absent/null, distinct from a complete measured zero.
Coverage failures preserve applicable lifecycle/process retention reasons.
Publication and observer failures cannot change the producer result.

Literal no-follow workspace/parent/root/manifest observations bracket collection;
changed identity, boot mismatch or stale/unordered observations fail closed.
Supplied coverage is recollected because a relative path alone does not correlate
its candidate identity. The observer has a 12-second admission deadline, a
30-second observer-local startup warmup, at most 64 candidates per discovery
pass (this bound is per discovery pass, not a cadence gate) and 256 inspected
discovery entries (including rejected names). The legacy report observation labels
`source="weekly"` and `observation_id="weekly"` are preserved historical identity
strings; they are not a cadence gate and do not change the daily scheduler cadence. Unattributable discovery
caps/unavailability produce bounded operational warnings, never fabricated task
IDs. Candidate failures are isolated. A blocking admitted OS/DB read cannot be
preempted; these bounds govern further work, not a hard syscall timeout.

The evidence collector observes full durable task/revisit/job/result records,
advisory sessions, and bounded Linux boot/PID/root/cwd/open-fd references. Terminal
flags and cleared trackers are not process-exit evidence. Partial, missing,
capped, warmup, changing, timed-out or unavailable evidence prevents eligibility;
independently complete process counts remain truthful even when lifecycle or
session state requires retention. The coverage collector compares two finite
boot-bound snapshots of the fixed workspace partition, with allocated bytes and
entries (hardlinks count per observed entry). Each source operation has shared
bounds and deadline admission; missing optional parents are not invented as
residual partitions. Repositories, special entries, malformed literal parents,
unknown, zero, incomplete or nonready coverage cannot authorize eligibility.

The collectors are now observed by these report-only production callers. The
reclamation hook separately makes its own fresh bounded consumer admission;
report-only observations neither seal an executable ledger nor authorize that
action. Legacy,
shared `/tmp` and pre-contract roots remain ineligible. Portable observations
cannot exclude future writers or a hostile same-UID race.

### Orchestration core

Each attachment storage_key can be claimed by one task only, including within
the same tree. Each direct, child or then-leg list allows at most five refs;
keys must be unique across the entire initial decision, including siblings and
nested legs. Carrier top-level refs belong to the carrier; its first leg reads
through ancestry without a second claim. Initial fanout preserves both child
and nested attachment fields; later chain advancement keeps its existing rules.

Recognized initial pre-child errors include too_many_attachments,
duplicate_attachment, task_attachment_not_found, attachment_already_claimed,
invalid_attachment_display_name and unsupported_attachment_type. Feedback gives
the code, escaped key when supplied, and remediation. Correct in a genuinely
new authorized session/result; never replay the consumed result. Each
decision-owning task has at most TWO committed attachment-error feedback
opportunities shared across sessions and initial delegate/fanout/parallel.
Committed A consumes a chance even if B/enqueue is lost. The third recognized
error uses the existing root ESCALATED or nonroot FAILED disposition. A valid
correction after two remains eligible under current authority and revision
limits. Other authoring, revise, slice and total-step rules stay unchanged.


- **Orchestrator & task state machine.** The daemon-side loop that advances each task one step at a time, drives manager-decision turns, spawns children, and records terminal state. Spec `docs/superpowers/specs/2026-04-14-orchestrator-daemon-design.md`; current contract `docs/agent-guides/orchestrator-contracts.md`; impl `runtime/orchestrator/run_step.py`, `runtime/orchestrator/orchestrator.py`.
- **Forward-only root-terminal worktree reconciliation.** Original winning structural roots in all four terminal states (`completed`, `failed`, `cancelled`, `superseded`), including NULL-parent workflow tasks, reconsider their parent tree once after teardown. Old child deferrals are advisory only at this pass; dirty/local-only/open/active/held/successor-used/protected/unknown paths retain. Shared fresh exact-HEAD Git preservation gives live owning branches precedence; recursive/action-time identity and unchanged same-user scanner gates remain. Committed start INTEGER identity is stable; interrupted/repeated notices never retry. Shared10s work/1s report/1s reap targets, numeric caps and exact frontier/remainder distinguish complete retention from partial action anomalies. Child/manual/daily policies remain separate. [Owner and evidence contract](orchestrator-contracts.md#forward-only-terminal-task-worktrees).

- **Task-owner decision loop & completion contract.** Task owners (task_type='task') end every turn with a `decision` (`delegate`/`fanout`/`done`/`escalate`; the `parallel` alias is accepted for `fanout`); subtask agents report a plain completion. **Only root tasks (`parent_task_id is None`) escalate to the founder; a non-root task that would escalate via `decision:escalate` instead fails and hands back to its parent, and bounded failure-recovery carries it up (THR-033 Change A).** `orchestration_step_count` remains monotonic telemetry and never independently escalates or fails a task. Contract and guide `docs/agent-guides/orchestrator-contracts.md`; implementation in `runtime/orchestrator/run_step.py`.
- **Authority-v2 refusal lifecycle (THR-277).** Refused roots keep the founder-escalation lifecycle. Refused children become terminal FAILED with `authority_v2_refusal:<code>` and the closed `authority_v2_refusal_task_failed` audit, then use the bounded parent-wake path; no child `escalation` audit or founder/thread escalation is emitted. Startup leaves an `admitted` non-escalate causal result unfinalized, unwritten, and unfenced so its ordinary or accepted-recovery consumer still runs.
- **Inline delegation chains.** A task owner can declare a multi-leg subtask chain inline via `then: [...]`; the orchestrator auto-advances routine legs on matching verdict without consuming orchestration steps. Spec `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md` (current); impl `runtime/orchestrator/chain.py`.
- **Task status model.** The canonical task status vocabulary and transition rules (`pending`, `in_progress`, `escalated`, `completed`, `failed`, `cancelled`, `superseded`). Under THR-037 Change B (Path B) a parent waiting on its children/jobs is `in_progress` with the reason in `block_kind`; the await-founder state is the top-level `escalated`; `cancelled` is a founder-initiated terminal. Specs `docs/superpowers/specs/2026-04-19-task-status-redesign.md` + `docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md` (current); current vocabulary `docs/agent-guides/orchestrator-contracts.md`.
- **Subtask / composite tasks.** Subtask agents spawn bounded subtasks under a parent task, for decomposing a single delegation into iterative steps. Spec `docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md`; impl in `runtime/orchestrator/run_step.py`.
- **Revisit.** `happyranch revisit <task-id>` spawns a fresh root task inheriting brief and team from a terminal predecessor; old lineage freezes. Specs `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`, `docs/superpowers/specs/2026-04-23-revisit-root-link-design.md`. See [Revisit](#revisit) below for traps.
- **Session-timeout auto-route (RETIRED — TASK-3604).** Automatic daemon successor creation on opaque agent failures has been removed per founder direction. Opaque failures now end FAILED and hand to the existing parent/founder recovery paths (bounded manager-wake, escalation, explicit founder revisit). Legacy `auto_revisit_of` audit rows remain readable for historical compatibility. Original spec `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md` (retired).
- **Cancel (race + actor attribution).** Founder/agent task cancellation with race-safe state handling and audit attribution of who cancelled. Specs `docs/superpowers/specs/2026-05-26-cancel-race-design.md`, `docs/superpowers/specs/2026-06-06-cancel-actor-attribution-design.md`; impl in task routes and run-step helpers.
- **Bounded failure-recovery (TASK-573 / THR-078 / THR-183).** When a subtask fails, the parent task is re-enqueued for a bounded manager-wake decision step (not cascade-failed). A linked historical failure remains durable causal context for the owning manager, never a runtime retry-ceiling escalation or upward cascade. A later COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors so a completed-child wake cannot select a stale reason. A manager may mechanically re-dispatch unchanged work or direct revised work with a valid predecessor link; the daemon neither compares briefs nor creates retry/successor loops. A manager may explicitly propose escalation through the configured THR-181 path; inactive/static policy is not evaluator CONTINUE, and committed escalations remain human-resolved. Passive pipeline carriers fail closed through their outer barrier with causal-leaf context, while fanout-dispatched `task` managers decide locally. Parent-wake deduplication is an in-memory, pending-only `TaskQueue.enqueue_if_absent` contract: a per-task reservation is claimed under the queue lock, generation-aware publication runs outside that lock, and the reservation is always released. Concurrent bounded producers lose to a reservation or pending item; refusal/failure permits a future wake, dequeue permits a later legitimate wake, unrelated FIFO entries are preserved, and ordinary task enqueue is not coalesced. Startup tests join every owned cleanup worker before another sweep, shared runner restoration, or queue assertions because durable settlement is not cleanup completion. Implementation: `runtime/orchestrator/run_step.py:_enqueue_parent_if_waiting`, `runtime/daemon/__main__.py:_sweep_on_startup`, and `runtime/daemon/queue.py:TaskQueue.enqueue_if_absent`. See [Bounded failure-recovery](#bounded-failure-recovery).

### Agent runtime & executors

- **Agent executors & permissions.** Pluggable executors (Claude, Codex, opencode, Pi) with per-executor sandbox/allow-rule generation and workspace bootstrap. Spec `docs/superpowers/specs/2026-04-20-multi-executor-design.md`; contract `docs/agent-guides/agent-executors-and-permissions.md`; guide `docs/agent-guides/agent-executors-and-permissions.md`; impl `runtime/orchestrator/executors.py`.
- **Manage-agent (enrollment).** Enroll, update, or terminate an agent; enrollment is founder-gated. Spec `docs/superpowers/specs/2026-04-17-manage-agent-design.md`; skill `runtime/skills/bundled/manage-agent/SKILL.md`; route `runtime/daemon/routes/agents.py`.
- **Manage-repo.** Add, remove, or update a repository in an agent's `org/agents/<name>.md` frontmatter. Spec `docs/superpowers/specs/2026-04-17-manage-repo-design.md`; CLI `happyranch manage-repo`.
- **Founder-facing executor-switch.** `happyranch set-executor` switches an existing agent's executor end-to-end in the org `.md` frontmatter and executor bootstrap (THR-095: agent.yaml is no longer synced). A real executor change clears the old executor-specific `model`; an idempotent request preserves it. The shared canonical instruction pair (`AGENTS.md` + `CLAUDE.md -> AGENTS.md`) and `.claude/skills` are preserved across a switch; only the executor-only `.claude/settings.json` is stale and WARNs by default; cleanup requires an explicit `--clean` flag. CLI-only — no web surface. Commit cf4c9e0; impl `cli/commands/agents.py`, `runtime/daemon/routes/agents.py`.
- **Per-agent memory.** Each agent keeps durable `MEM-NNN` learnings plus task recall. Specs `docs/superpowers/specs/2026-04-18-agent-memory-design.md` (superseded), `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`; impl `runtime/infrastructure/learnings_store.py`. See [Per-Agent Learnings](#per-agent-learnings) below for traps.
- **Retired System Assistant.** Feature routes/CLI/settings/dock are removed. Legacy files, links and historical records remain inert.
- **Jobs.** Background subprocesses run by the daemon, with two policy flags (`review_required`, `persistent`) and founder-review gating. Spec `docs/superpowers/specs/2026-05-26-jobs-design.md` (current); skill `runtime/skills/bundled/jobs/SKILL.md`; impl `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`. (Jobs absorbed the earlier "agent script requests" feature, `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md`, now superseded.) See [Jobs](#jobs) below for traps.
- **Task blocked by job.** A task can self-block on one or more jobs via `tasks.blocked_on_job_ids`; it auto-resumes when all are terminal. Spec `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. See [Task Blocked By Job](#task-blocked-by-job) below for traps.
- **PR CI wait / guarded merge.** PR-producing engineering tasks use the jobs + `blocked_on_job_ids` path to wait for GitHub CI outside the agent session. Two CLI entrypoints (`python -m runtime.daemon.pr_ci_waiter` and `python -m runtime.daemon.pr_ci_merge`) wired to real `gh` provide the polling and guarded-merge mechanisms. The poll job (submitted through the existing jobs path with `review_required=false`) polls checks for a pinned PR head SHA, handles no-checks-yet settling, detects stale heads and timeouts, and prints a structured verdict JSON. The poll job performs NO merge. On resume, the task owner triggers `guarded_merge` as a short daemon-run step; it re-enforces all guards (review APPROVE + QA PASS + CI PASS + unchanged SHA + mergeable CLEAN) before merge. No new daemon route, no new task state, and no raw `gh pr merge` permission broadening. Current contract: `docs/agent-guides/orchestrator-contracts.md` (including the **Merge-evidence contract** — the canonical vocabulary `APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL` and the structured-vs-prose extraction rules: a NON-NULL structured `verdict` is primary; serialized `null`, the durable recall producer's representation of legacy/no-structured rows, uses the strict annotated-prose fallback); implementation: `runtime/daemon/pr_ci_waiter.py`, `runtime/daemon/pr_ci_merge.py`.

### Collaboration surfaces

**THR-289 thread-list contract.** `GET /threads?page_size=50` returns bounded rows plus org-wide `totals` (`open`, `archived`, `all`, `dream_origin`), `has_more`, nullable `next_cursor`, and `sampled_at`. Counts ignore the client filter and loaded depth. Rows, totals and page-limited participant projection share one owned SQLite read snapshot under the existing RLock; releasing or rolling back that savepoint does not commit or roll back an ambient caller transaction. Continuations retain the complete stored ordering tuple, need no surviving anchor, and are hints rather than a frozen cross-request snapshot. A fresh traversal after changes quiesce yields the complete current set and order. Open sorts pin rank/numeric suffix/start/BINARY ID; Archived sorts coalesced archive/start/BINARY ID; All sorts global start/BINARY ID. Legacy omitted pagination is unchanged, including unrestricted status and zero/negative limits.


- **Threads.** Founder-visible coordination and cross-team handoff conversations with per-thread recipient-set routing (THR-198): mention routing and the strict mention-led exchange are **UNCONDITIONAL** (founder ruling TASK-6027) — valid current-participant @-mentions narrow conversational REPLY wakes to exactly that set (speaker excluded), zero valid mentions broadcast to participants (there is no disabled-thread state and no routing/exchange switch, route, CLI verb, UI control, or config key: the per-thread mention-routing setting, the proposed `reply_exchange_enabled`, and the org kill switch were all removed; the shipped `mention_routing_enabled` column is inert legacy compatibility storage only — never read to alter behavior, never exposed on the wire), priority/fallback is present (frozen priority cohorts, 5-minute grace, 4-hour fail-open), and TASK_FOLLOWUP/BOOTSTRAP wakes are isolated and never mention-routed; dispatch from a thread is self-only. Threads carry composer attribution (`composed_by`, `composed_from_task_id`, `composed_from_dream_id`) — the dream marker identifies dream-originated founder threads. Specs `docs/superpowers/specs/2026-05-13-threads-design.md` and successors (broadcast-only, agent-initiated, markdown composer, task-followup, escalation surfacing, working indicator, close-out removal/resume, file attachments); impl `runtime/infrastructure/thread_store.py`, `runtime/daemon/thread_runner.py`. See [Thread Broadcast Routing](#thread-broadcast-routing), [Thread Agent-Session Resume](#thread-agent-session-resume), and [Thread Task Followup](#thread-task-followup) below for traps.
- **Thread rename + pin (THR-209 Phase 1).** Founder-only thread-organization controls. Rename edits the durable `subject` (`POST /threads/{id}/rename`; trim, non-empty, ≤120 chars, duplicates allowed, last successful save wins); pin/unpin is durable founder-workspace presentation state stored in an additive nullable `threads.pinned_at` column (`POST /threads/{id}/pin`, strict bool). **Invariants:** both are presentation/organization controls — they never create a thread message, never send a notification, never touch participants/unread, and never change activity timestamps (`started_at`/`archived_at`); identity (`id`, URL, participants, routing, lifecycle) is immutable under rename/pin. Pinned threads rank above unpinned (Pinned section) in **open-thread views only**, ordered by immutable numeric thread ID descending (THR-10 above THR-2, never activity/lexicographic); ordinary (unpinned) order is unchanged; archived/closed views have **zero pin presentation** (one ordinary list, no pinned/unpinned split, no pin rank) while the durable pin state is retained for reopen/restore, and the status-less query/"all" merge are ordinary views too; pin state lives on the thread row, so deleting a thread removes its pin state automatically. List rows are navigation-only; Pin/Unpin remains in the thread detail header. The web optimistic cache reorders open lists under the same numeric rule immediately (before response/refetch) and archived/status-less caches never reorder — no client/server semantic divergence (TASK-5987). Audit rows `thread_renamed` / `thread_pinned` / `thread_unpinned` use the existing `audit_log.task_id` = THR-* scope convention and never appear as thread messages. Each mutation is ONE rollback-safe transaction under the org `db_lock` (`rename_thread_with_audit` / `set_thread_pinned_with_audit` in `runtime/infrastructure/database.py`): authoritative old-value read + idempotence decision + `subject`/`pinned_at` write + audit row commit atomically; on audit failure everything rolls back (no durable unaudited transition, error response). Concurrent renames are last-successful-save-wins with a truthful sequential old→new audit chain; concurrent same/opposite-state pins emit exactly the audit rows for the durable transitions (true no-ops unaudited). Spec `docs/superpowers/specs/2026-08-25-thread-rename-and-pinning-design.md`; routes `runtime/daemon/routes/threads.py`; wire adds `pinned`/`pinned_at`/`last_activity_at` to thread rows; web UI `web/src/features/threads/ThreadsPage.tsx` (Pinned section, inline rename, detail-header pin controls).
- **Thread escalation surfacing.** When a thread-dispatched task escalates to `escalated`, the runtime injects a `task_escalated` system message into the originating thread and re-invokes the dispatching manager for a founder-facing followup — mirroring the terminal task-followup. Rendered in both web (ThreadsPage.tsx `task_escalated` case) and CLI (`thread forward`). Spec `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md`; impl `runtime/orchestrator/run_step.py`, `runtime/daemon/thread_runner.py`.
- **Tasks surface.** Org-wide work list (not a kanban) with roots-only default view, group-by segmented control (Status / Agent / Thread), severity rollup per root reflecting the worst **current** status of its subtree (DERIVE over `parent_task_id` children — no schema change; `GET /orgs/{slug}/tasks/roots`). A historical FAILED descendant whose forward same-parent `revisit_of_task_id` lineage leaves no unresolved FAILED leaf (a COMPLETED/SUPERSEDED/active/cancelled successor, or a malformed cycle handled conservatively) does not dominate; only a FAILED descendant contribution is curated, while every other status, the root's own severity and all escalations are preserved, and root-level revisit links are never followed. See [Bounded failure-recovery](#bounded-failure-recovery). `Waiting on you` is a separate exact-`status=escalated` roots traversal, not an inferred ordinary-page/severity-rollup count or ordinary filter projection; it task-id-deduplicates and becomes exact only after cursor exhaustion. When non-empty it renders as the FIRST ordinary-styled group inside the shared list shell (same `tasks-group` wrapper, group heading and raised rows-card as every status group) directly after the task column header and above the status groups — never a separate outer card — and it is absent (no group, heading or empty card) when empty and attention is not loading/errored. A failed attention refresh or continuation retains known rows/count with a visible error state, and continuation retry uses that stream's cursor. Its page-local Waiting controls wrap within the group at narrow widths without changing the shared Button primitive, and a non-empty escalated group may coexist with ordinary loading/error/empty states without hiding the shell or contradicting visible rows. Bidirectional lineage inline (\u2190 supersedes / \u2192 revisits) backed by `revisit_of_task_id` + `get_direct_revisits()`. Task detail pane with connected vertical chain timeline (`walk_revisit_chain()`), blocked node naming its blocker, property rail, append activity log, and raw monospace brief with "Show full" toggle. State vocabulary: Loading (skeleton rows by group), Empty per group, Error-with-retry. Keyboard: \u2191/\u2193 move selection, Enter opens, Esc clears. Spec `docs/design-overhaul/product_lead-2026-06-17-design-overhaul-PRD-final.md` (\u00a74.3); web `web/src/features/tasks/TasksPage.tsx`, `TaskDetailPane.tsx`; route `runtime/daemon/routes/tasks.py` (`/tasks/roots`).
- **Knowledge base.** Per-org shared, durable cross-agent knowledge (rules, references, founder rulings); orgs do not share a KB. Contract `docs/agent-guides/features-and-invariants.md`; impl `runtime/infrastructure/kb_store.py`, `runtime/daemon/routes/kb.py`. See [Knowledge Base](#knowledge-base) below for traps.
- **KB view tracking.** Agent-CLI KB entry read counting scoped to agent consults only (founder ruling THR-009). Distinguished from web reads via `X-HappyRanch-Surface: cli` request header (a source label, not auth). Read surface is CLI-only: `happyranch kb stats` renders a table ordered by view count; no web surface. Spec `docs/superpowers/specs/2026-06-10-kb-view-tracking-design.md`; impl `cli/commands/kb.py`, `runtime/daemon/routes/kb.py`, `runtime/infrastructure/database.py` (`kb_views` table).
- **Shared artifacts.** Per-org opaque file blobs produced by one agent and visible to all agents in the org. Impl `runtime/infrastructure/artifact_store.py`, `runtime/daemon/routes/artifacts.py`; CLI `happyranch artifacts {put,list,get}`. See [Shared Artifacts](#shared-artifacts) below for traps.

### Org & runtime

- **Multi-org runtime.** A single daemon hosts multiple orgs in parallel under a schema-v2 container (`<runtime>/orgs/<slug>/...`); per-org routes live under `/api/v1/orgs/<slug>/...`. Specs `docs/superpowers/specs/2026-04-26-multi-org-runtime-design.md` (superseded), `docs/superpowers/specs/2026-04-28-parallel-multi-org-runtime-design.md`; current shape `docs/agent-guides/project-layout.md`; impl `runtime/daemon/org_state.py`, `runtime/daemon/runtimes.py`.
- **Org content model.** Each org is loaded from `org/` — charter, `teams.yaml`, per-agent `agents/*.md`, and `config.yaml`. Guide `docs/agent-guides/project-layout.md`; impl `runtime/orchestrator/org_config.py`, `runtime/orchestrator/teams.py`, `runtime/orchestrator/agent_def.py`.
- **Org portability (Slice A).** CLI-only, relocation-only preflight + reconciliation. See [Org Portability](#org-portability-thr-187-slice-a) below. Impl `runtime/portability/` (pure classifier + eligibility), `runtime/daemon/routes/portability.py`, `cli/commands/runtime.py`.
- **Token-usage tracking.** Per-task, per-agent, thread-scoped, dream-scoped, and work-hour-scoped token accounting with two complementary legacy metrics: **churn** (`churn_tokens` = input + output + reasoning, the cache-excluded value used for existing ranking/thresholds) and **context** (`context_tokens` = churn + cache_read + cache_creation). Codex ingest keeps issue #216's net-of-cache input normalization and now preserves integer `cache_write_input_tokens` exactly, including `0`; absence/non-integer stays `NULL`. The Usage v1 pure normalizer distinguishes reported zero from `not_reported`, exposes uncached-only Fresh input as partial when cache write is absent, and counts reasoning once from declared parser semantics. Lifecycle cohort identity is captured when a run starts: thread rows carry nullable executor/model, task `session_start` carries session ID, actual spawn purpose, executor/model, and `dream_started` carries executor/model. `thread_invocations.reply_message_seq` is the nullable, transaction-owned link to the one persisted reply message; NULL means “reply outcome not recorded” (legacy or no persisted message), never an inferred negative. Missing configured models and pre-change history stay unattributed/NULL and are never guessed or backfilled. The lifecycle-first Workload/Efficiency read model is exposed through bearer-authenticated `GET /usage/workload` and `GET /usage/efficiency`; Workload rows require current-window activity, Replies count only linked consumed REPLY wakes, recorded/total-consumed reply outcome coverage is explicit, and any unknown outcome withholds only the Replies comparison. Missing/ambiguous usage remains in lifecycle denominators, and dream usage correlates by dream ID + agent rather than provider session ID. It never consumes legacy churn, whose Codex reasoning double-count is intentionally unchanged. Specs `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`, `docs/superpowers/specs/2026-06-08-thread-talk-token-usage-scope-design.md`, and `docs/superpowers/specs/2026-09-30-usage-v1-design.md`; APIs `runtime/daemon/routes/tokens.py` and `runtime/daemon/routes/usage.py`; CLI `happyranch tokens` (issue #216).
### Web & CLI

- **Settings narrow layout (THR-118 W5b).** Below640px the four sub-nav links wrap above the panel; wider screens retain the left rail. Preferences has locally bounded, wrapped full copy and narrow-screen padding. Routes, active state, icons, API gates, client-only Preferences independence and all handlers/state/focus remain unchanged.
- **Browser locale activation (THR-118 W5b).** `main.tsx` synchronously selects full mode and hands one resolution through App/AppShell before the first React text. AppShell also selects full mode for later storage events. Valid saved en/zh-CN wins the environment; absent/invalid values follow ordered supported browser languages, then English. Startup performs no preference write. Storage delete/clear resolves the same full fallback without echo. Resolver/provider API preview defaults, native authority and sequenced acknowledgements remain unchanged. After System Assistant retirement, inventory is 20 translated/3 not-applicable, supported by the accepted finite W5a map rather than exhaustive coverage claims. Native N0/N1 and deployment/live verification remain deferred/separate.

- **Web loading and modal copy (THR-118 W5a).** Mounted dialog close controls use `common.close`. Markdown’s optional `mermaidLoadingLabel`, forwarded by `MessageBubble.labels.mermaidLoading`, localizes only per-block lazy-loading chrome; omitted props retain English. KB, Task detail/recall, Threads provide the catalog label. Renderer identity and loaded SVGs remain stable on locale changes; authored text/code and Mermaid failure source stay verbatim. Namespace markers are inventory, not rendering acceptance. W5a finite coverage is accepted (PR993); W5b enables full browser-language defaults and bilingual availability copy. Native N0/N1 remain deferred. Pending-import assertions live in the isolated Markdown.loading.test.tsx file; Markdown.test.tsx SVG/error cases use an independent lazy-module instance so filters and file order do not depend on releasing another test’s import.
- **Work Hours reachability (THR-118 W5a).** Local heading/actions wrap within agent detail; both reconciliation and roster preserve every raw column/value in localized named focusable horizontal scroll regions. Arrow keys reach the final effective/eligibility column. Shared primitives and editor/navigation/save semantics remain unchanged. Historical document-width assertions did not establish essential-child reachability; keep the historical clipping evidence and original source/build limits. W5a finite coverage is accepted with PR993; W5b enables full browser-language defaults. The tier editor now stacks field rows below the small-screen breakpoint, bounds grid children and field/control groups, wraps resets/day controls and full raw title/provenance/selected-timezone text, and allows the existing dialog to scroll vertically. Desktop retains the row hierarchy. Editor child and text-range bounds must be measured against actual dialog content and viewport after scrolling, with pointer hit testing and a real Tab cycle; page/table bounds alone do not establish editor readability. The affected browser cases include all three tiers, windowed/continuous and impact stages, long raw values, both locales at390/1440, and both-direction node/focus/selection/draft preservation with zero locale HTTP. Actual Save/PUT, reset-null and verbatim422 contracts remain owned by TierEditorDialog.test.tsx and work-hours.i18n.test.tsx; screenshots alone do not prove writes.

- **Web UI.** Usage v1 app-owned Workload/Efficiency presentation is translated in en/zh-CN (THR-118 W4d-2); locale switching preserves Compare/cohort/focus and issues no API requests. Org-wall-clock window parts do not shift with viewer TZ; instants use the response timezone. W5b enables full browser-language defaults. React SPA with a flat primary sidebar nav (Home · Threads · Tasks · Jobs · Todos · Agents · Work Hours · Skills · Knowledge · Artifacts · Audit · Dreams · Usage · Health), footer-pinned Settings + account row (founder-approved flattening: THR-140 seq 208 / PR #644 superseded the #633/#577 grouped-nav pilot), org switcher at the sidebar top, theme toggle in the AppBar; desktop window chrome, served from `web/dist/`. **Default landing route is Home** (the Dashboard page at `/orgs/:slug/dashboard`). Specs `docs/superpowers/specs/2026-05-14-web-ui-design.md`, `docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md`, `docs/design-overhaul/product_lead-2026-06-16-design-overhaul-PRD-build-spec.md` (Direction-A IA); architecture `web/ARCHITECTURE.md`; guide `docs/agent-guides/web-and-cli.md`.
- **CLI.** `happyranch`, a thin HTTP client over the daemon API used by both the founder and agents for all side effects. Guide `docs/agent-guides/web-and-cli.md`; impl `cli/`.
- **Audit log.** Append-only record of every state-changing action, keyed by task id (with scope prefixes for non-task actors). Impl `runtime/infrastructure/audit_logger.py`, `runtime/daemon/routes/audit.py`; CLI `happyranch audit`.
- **Token-usage visibility (Phase 1 dashboard panel).** A `TopTokenThreadsPanel` on the org dashboard showing thread-scoped token spend ranked by total tokens, plus CLI drill-down (`happyranch tokens --by-thread`). This is a **dashboard panel**, NOT a dedicated page. The underlying token-accounting infrastructure (per-task, per-agent, thread-scoped) is documented under [Token-usage tracking](#org--runtime) below. Commit f1dd539; impl `web/src/features/dashboard/components/TopTokenThreadsPanel.tsx`, `cli/commands/tasks.py`.

### Background / reflection

- **Nightly dreaming.** Private scheduled per-agent reflection runs, separate from tasks and threads, that may write learnings, propose KB candidates, and open a founder-only thread on meaningful output. Dream-originated threads carry the `composed_from_dream_id` marker (A4 migration, design-overhaul). Spec `docs/superpowers/specs/2026-06-09-nightly-dreaming-design.md`; impl `runtime/infrastructure/dream_store.py`, `runtime/daemon/dream_runner.py`, `runtime/daemon/dream_scheduler.py`, `runtime/daemon/dream_queue.py`, `runtime/daemon/routes/dreams.py`. See [Dreams](#dreams) below for traps.
- **Per-agent work-hours / scheduled wakes.** Founder-configured per-agent work windows (windowed or continuous) that wake idle agents on schedule to self-dispatch routine tasks parsed from per-agent `org/agents/<name>.md`. Backed by a `work_hours` table mirroring the dreams data model. Founder-facing `happyranch work-hours status|list|show` plus the agent wake callback `spawn`. Funded as #92. **Web UI (design-overhaul, THR-035 consolidation):** the wake-execution list (formerly a standalone Schedule surface at `/orgs/:slug/schedule`) is now the **Wakes** in-page tab of the Work Hours surface at `/orgs/:slug/work-hours?view=wakes`; old `/schedule` bookmarks redirect. Lists per-agent work-hour wakes grouped by agent with real stored fields (date, slot, mode, scheduled-for, status, routine count, spawned task IDs via IdBadge click-through). No authoring controls — creating named recurring wakes is deferred (D6). Spec `docs/superpowers/specs/2026-06-10-working-hours-design.md`; impl `runtime/daemon/work_hours_scheduler.py`, `runtime/daemon/wake_runner.py`, `runtime/daemon/wake_queue.py`, `runtime/daemon/routes/work_hours.py`, `runtime/infrastructure/work_hours_store.py`, `cli/commands/work_hours.py`, web `features/work-hours-config/WakesView.tsx`. The consolidated Work Hours surface (config overview + wakes tab) lives in `web/src/features/work-hours-config/`.

## Org Portability (THR-187 Slice A)

Slice A is **preflight + reconciliation only** — no archive, export, import,
staging, transfer fence, source deletion, workspace/task-output transfer, or
cancellation of live work. It is CLI-private (`happyranch orgs ...`); there is
no UI, TS client, or browser contract.

- **`happyranch orgs portability-preflight <slug>`** — read-only, founder-
  authenticated. Exhaustively classifies every direct org-root child exactly
  once as `include`, a *named* `exclude`, or `reject`. Allow-list: `happyranch.db`,
  `org`, `artifacts`, `kb`, `threads`, `task-attachments`, `jobs`, `dreams`,
  `work_hours`, `schedules`, `talks`, conditional valid legacy `skills`, and only
  `workspaces/<agent>/memory/**`. Generated markers, derived projection, WAL/SHM
  sidecars, caches, zero-byte legacy residue, and non-memory workspace data are
  named exclusions; unknown/nonregular/nonzero-residue/invalid-skill roots reject.
  Quiescence: refuses any pending/in_progress/escalated task (live, delegated,
  or job-parked), active session/PID or queue entry, pending thread invocation,
  pending/running job/dream/work-hour, or any armed/firing schedule. It *reports*
  possible zombies; it never resolves them.

  **Conservative schedule policy (founder).** Preflight refuses when **any**
  schedule is **armed or firing**; there is no relocation-specific disarm
  command or export fence. The response reports only *existing* controls as
  actionable remedies: an **armed** schedule →
  `happyranch todos pause --org <slug> <schedule_id>` or
  `happyranch todos cancel --org <slug> <schedule_id>`; a **firing** schedule
  → no pause/cancel is permitted, so the correct non-mutating remedy is to wait
  for it to reach a terminal state and re-run the preflight (no new control);
  live nonterminal tasks/active jobs → `happyranch cancel <task_id> --org
  <slug>` / `happyranch jobs stop <job_id> --org <slug>`; active sessions,
  queued items, pending invocations, dreams, and work-hours have no founder
  cancel control → wait/resolve; and a confirmed zombie → the founder-only
  `happyranch orgs reconcile-portability <slug> --from-file <absolute-json>`
  path. Preflight is read-only and never invokes any of these controls.
- **`happyranch orgs reconcile-portability <slug> --from-file <request.json>`** —
  founder/master-bearer only (reuses the existing human-authority dependency).
  Names exactly one candidate + evidence/disposition; revalidates a true zombie
  under the org DB lock; invokes the shared result/terminalization seam
  (`_consume_completion_report` for an orphaned result, or the reaper's
  `cancelled` transition). Audits actor, SHA-256 request hash, evidence,
  disposition, and before/after state under the ordinary `task_id` scope. A
  delegated/job-blocked task is never a zombie merely because it is old.
  Preflight never calls reconciliation; reconciliation has no export-cancellation
  path.

## Knowledge Base

Per-org KB entries live under `<runtime>/orgs/<slug>/kb/`. Orgs do not share a KB. `KBEntry.type` is freeform; route validation only enforces non-empty `slug`, `title`, `type`, and `topic`.

The dedicated `kb precedent` route was removed. Founder rulings flow through `happyranch kb add` with `source_task: <task-id>` in frontmatter.

Implementation: `runtime/infrastructure/kb_store.py` and `runtime/daemon/routes/kb.py`. Full rules: `docs/agent-guides/features-and-invariants.md`.

## Per-Agent Learnings

### Memory telemetry guard (THR-091, TASK-7767)

Task bootstrap retains text and item IDs from one actual `MemoryStore.render_memory_digest` pass; `build_memory_digest` remains a compatible string/None wrapper. Only appended full blocks or pointer lines contribute IDs. Directive fit is full-body exposure; budget fallback is pointer exposure. IDs merely mentioned in bodies, titles, header, or nudge are not item exposure. Scoring, ordering, salience, character budgets, prompt bytes and nudge behavior remain unchanged. A header/nudge-only result has no items and emits no impression. Only appended items with string IDs satisfying the existing `ID_RE.fullmatch` contribute identity metadata. Null, missing, nonstring or malformed IDs remain rendered byte-for-byte under the existing permissive parser, but contribute no identity or ID fragments from their representation, title or body. A malformed-only digest still launches normally and emits no impression; valid neighbors retain exact modes/counts. Strict writer validation still rejects malformed caller-supplied metadata before insertion.

After trusted task/session binding and before launch/session_start, one existing `memory_digest_impression` stores accurate `digest_ids`/`digest_count` with exactly three new keys: `memory_telemetry_version=1`, `pointer_ids`, `full_body_ids`. Lists are unique and disjoint, with union equal to the digest IDs/count. If duplicate files render one ID in both forms, the observed full body owns that ID; no duplicate opportunity is recorded. No prompt, title, body, query or brief is logged. Optional metadata rejects inconsistent types/version/duplicates/overlap/union before insertion. Logger calls without metadata retain the byte-equivalent old unversioned payload; no history is inferred, upgraded or backfilled. Existing audit action and actual task row scope stay unchanged.

The unchanged source resolver checks accurate digest IDs before validated search results, so a body-mentioned but nonrendered `MEM-999` can receive search attribution after an actual search, while the shown item stays digest-sourced. Read/search writers, SessionTracker validation, task/session identity and eligibility are unchanged. Recovery/unattributed rows gain no task eligibility from a version field. Exposure metadata alone leaves both report consumers fail-closed. G1's independent role/job/probe and current-serving census validation is described below; actual installed canary acceptance remains a separate operational gate. `session_start` records intended invocation, not a complete process-launch census. No canary is accepted, clean epoch started or collection enabled by this metadata.

The source-side G3 observer is attached by real `OrgState` construction before
workers. One actual `_run_agent` entry reserves an independent boot-local ordinal
before telemetry writes. Metadata-only `memory_runtime_intent`,
`memory_runtime_identity`, `memory_runtime_expectation`, `memory_runtime_binding`,
`memory_runtime_launched`, `memory_runtime_terminal` and `memory_collection_seal`
use the existing actual-task audit scope. SID is observed at its original
assignment; parent relation and recovery purpose come from runtime facts,
never provider resume or request claims. Provider retries add started-callback
occurrences under one logical invocation. Each terminal belongs to its own
ordinal/runtime SID; no latest-agent session-end inference is used.

Expectation freezes the same structured render/config used by the unchanged
prompt: `disabled/budget_zero`, `empty/memory_directory_absent`,
`empty/renderer_empty`, `empty/no_valid_rendered_ids`, or
`nonempty/rendered_ids`. Text presence is separate from eligible item exposure;
full-body-only exposure is nonempty with zero pointer opportunities. No content
is recorded and no missing expectation is inferred from an absent impression.

Live attempted/persisted counts and chained digests are independent of stored
rows. A short metadata lock never spans rendering, execution or application
callbacks. Observation-boundary persistence drains concurrent metadata in
generation order; another invocation's callback need not wait on a blocked
observer writer. Writer failures are sticky and affect observation availability
only. Seals checkpoint independent counters/digests without reading history;
their diagnostic integrity is `census_not_reconciled`, never a validation or
health claim. Exhaustive zero-write read validation rejects missing/duplicate/
corrupt phases and seals, missing starts or expected impressions at zero reads,
unresolved population/parent/type facts, unknown/pending preparation and zero
population, subject only to the closed failed-before-exposure class below.
It compares opening/closing semantic observer facts (including
errors, pending writer/preparation state and seal progress), excluding sample
timestamps. Audit, task and result history is acquired in primary-key pages
of at most 256 rows, with no shared Database lock across pages or decoding,
and a 100,000-row bound per table including unrelated/prior-boot history.
Same-connection write counts
and other-connection data versions bracket acquisition. Movement, read failure
or work exhaustion returns unavailable; no partial history is validated and no
retry-until-quiet loop or reader write is performed. A restart allocates a new
observer boot and never reconstructs old completeness. Constructor/attachment
failure preserves ordinary org startup with explicit unavailable observation.

Founder THR-091 seq428 permits one closed failed-before-exposure class. Exactly
one ordered authenticated intent -> identity -> raised terminal must agree on
org/org-root/boot/ordinal/task/agent/runtime SID, actual parent/type/population
and supported failed-task lifecycle. The terminal requires Boolean false
`expectation_known` and `binding_known`, INTEGER zero `launched_callbacks`
(excluding Boolean/float/string zero), and explicitly null `success` and
`returncode`. Missing fields, unknown facts, duplicates or contradictions refuse.
Complete bounded audit/task/result acquisition must prove absence of expectation,
binding, session_start, impression, launch, result callback and credited
get/search for that exact tuple. No filtered stream or empty default supplies
absence proof. Independent attempted/persisted counts/digests, all ordinals and
full seal history remain mandatory, with stable semantic bookends and no error,
pending writer or preparation. Observer acquisition pages all three tables in
at most 256-row SELECTs with a 100,000-row bound per table and revision bookends.
The failed ordinal remains explicitly classified in internal census results and
all full counts/digests/seals; it creates no shown/natural-session denominator,
expectation, binding, start, callback or credit. All-failure/zero qualifying
populations remain insufficient; ordinary exposed and lawful in-flight checks
remain. Other retry/resume/SID evidence stays separate. This classification
supplies no B/C/health/epoch acceptance, temporal POST-retirement zero, source/
boot/cohort authority, threshold change, reset or history rewrite.

The seal-action audit GET now supplies an optional closed current-serving
observation from the actual OrgState, with explicit unavailable/unknown fields,
paired live counters and bounded loaded/disk/interpreter/registry identities.
It does not write rows, reseal or produce an epoch. G1 validates complete
acceptance evidence and current census before projecting existing epoch references.
Other audit response shapes remain unchanged. The exact response and nested
identity are specified in the corrective memory spec; a stable empty N0 view
is descriptive and unaccepted.

This census and its seals are not independent installed acceptance. G1's
opt-in `MemoryCollectionV1:` summary protocol authenticates exact admitted
manager/QA rows, registered roles, prior finite original delegation/command,
owned terminal job/audits/complete outputs and real ROOT/CHILD operations.
Only final post-log reconciliation may append an epoch or invalidation; both
successful result-log arms use their exact persisted result ID. Parseable
summaries, fixtures, versions or flags cannot establish health.
The assigned QA must differ from the makers in every preapproved ROOT/CHILD
slot as well as from the manager; registered worker status alone is insufficient.
Each original probe task must also remain completed, uncancelled and bound to
its recorded maker/team. A successful earlier session on a subsequently failed
probe task cannot provide positive health evidence.

New initial/reset admission requires each probe age in [0,48h] at the FINAL
server commit. Reports and equivalent replay authenticate that original age
against the immutable original commit, while rechecking original evidence and
CURRENT complete source/import/loaded-code/boot/cohort/profile/path/census.
Elapsed age alone does not expire an accepted epoch. Replay neither renews nor
replaces QA; fresh reset requires exact predecessor and fresh independent QA.
Withdrawal remains possible after stale or unhealthy proof. Damaged, lost,
ambiguous, partial or moving evidence closes collection with no latest fallback.
Readers never write, reseal, repair, invalidate or start epochs.

Canonical CLI task sweeps and HTTP list/detail/two-capture comparisons share one
semantic task projection. It excludes only valid rendering timestamps at
`pause.observed_at`, `pause.blockers[*].observed_at` and `child_wait.observed_at`.
Malformed timestamps/structures, unknown fields and every other `observed_at`
remain evidence. Raw acquired records remain intact. Task lifecycle/owner/SID/
lineage/content, pause/wait states, result/job bytes, roles and all serving
counts/digests/seals/errors/generations remain compared. Real serving movement
(including generation53->54, seal312201->312222 and terminal6->7) refuses;
timestamp equality cannot retroactively qualify an earlier moving capture.

Backend and canonical CLI JSON/text share the report core. G1 acquisition
exhausts original tasks/results/jobs/audit evidence with stable bookends and
complete owned outputs. No-epoch diagnostics exhaust all relevant memory and
lifecycle action pages plus a coherent complete task snapshot and roles before
empty/short returns. Unrelated audit history does not consume their existing
100000-row bound; authority readers retain the default unfiltered snapshot. HTTP/decoder/timeout, cursor or
SELECT failures refuse as `acquisition_unavailable`, without partial CLI stdout.
Task pages use `(created_at, id)` descending, with parsed wire timestamps and
the last ID as the server-resolved transport cursor, rather than ID ordering.
Natural population excludes designated probes/retries, recovery and all
pre-start invocations from qualifying metrics. Credited late events on old
tuples remain visible to the retirement diagnostic and can close eligibility. D1 remains the later of
original epoch start and first qualifying natural impression, counting complete
UTC days only. Valid short samples remain `insufficient_sample`.

D reporting (THR-091 seq389–390) adds `by_session_kind` and
`session_lifecycle` to both backend and CLI JSON/text. The disjoint kinds are
`first_launch`, `job_resume`, `retry`, `retry_job_resume`, `other_continuation`
and `unknown`. A runtime-written `tasks.revisit_of_task_id` identifies a retry;
a unique actual `task_resumed_from_jobs` between the previous same-task start
and this start proves a job resume. A later session alone is an
`other_continuation` (or `retry` with a diagnostic flag), never an inferred job
resume. Kind correlated-session totals reconcile to the aggregate; lifecycle
session counts additionally include credited old tuples inspected for damage.
Per-kind pull-through is diagnostic only and introduces no thresholds.

Retirement joins exact boot/ordinal/task/agent/SID runtime identity, launch and
terminal with session start/end. SID-less `session_end` joins only a unique
ordered task/agent interval ending at its own runtime terminal. Natural and
excluded recovery/thread/dream intervals participate in end ownership before
natural-only classification and source validation. Audit IDs resolve
ordering, including equal timestamps; no latest-agent inference is used. Any
credited read **or** search after end, terminal or a later same-task start is
reported per kind, excluded from valid read metrics and closes collection
eligibility under the existing explicit reset contract. Valid earlier history
is retained. Missing, duplicate, misjoined or ambiguous lifecycle/lineage is
unknown, with null hard-zero diagnostics where evidence is unavailable. An
exact current in-progress task/agent/SID may lawfully remain active without a
terminal. Empty kinds say `no_observed_population`, never PASS. These read-only
diagnostics cannot detect which caller supplied another *live* task's SID;
TASK-11402/JOB-4624's foreign-credit defect remains unaccepted pending the
separately authorized caller-context decision. No control row is written.

Source tests and source publication do not accept a production canary, start
an official epoch, or fulfill the independently owned durable health check
within48h AFTER a future accepted production epoch. Missing/late/in-flight/job-
error receipts leave that duty unfulfilled; only measured health failure closes
through health predicates. Installed/provider/deferred venues, deployment and
14-day/500 production follow-through remain separately owned operational gates.

Structural returned-data corruption produces the full empty-metrics error object
(null first event, days0, empty aggregate/by_agent/by_role/read_counts, explicit
errors). Task-only sources are `digest`, `search`, `explicit_or_other`; unsupported
strings use `invalid_source`, nonstrings `malformed_read_source_type`, and causal
or digest-first contradictions `source_contradiction`. Independently identified
manual/thread/dream/recovery/legacy populations are excluded before task source
validation. Runtime task credit requires the actual task/agent/runtime-session
start tuple; prefixes, client claims and SID-only matching cannot establish it.

Version1 observed pointer/full-body lists are validated, disjoint and duplicate
free, with union matching digest IDs/count. Legacy impressions remain unversioned
with unavailable mode metrics; no memory-file/body/directive inference occurs.
Exact tuple+memory pairs deduplicate opportunities and reads; secondary activated
sessions and per-agent/role/memory operation counts remain descriptive. Search
ratios use distinct persisted, causally corroborated search-sourced read pairs.
Earliest qualifying impression is a deterministic aware-UTC minimum. Elapsed
complete UTC days exclude partial first/current days. Raw day/session sample
flags may be true; without valid G1 authority, thresholds,
diagnostics_valid_for_collection and evaluation_candidate remain false,
decision `insufficient_instrumentation`. With valid authority, retain14 complete
UTC days AND500 qualifying sessions, per-agent30, aggregate<10% AND strict
eligible-agent majority, and >25% distinct validated search-read pairs with
eligible-role>=30-pair corroboration. Zero-pointer agents do not vote. Repeated
gets retain operation counts while distinct tuple/item pairs own rates.
Full-body-only natural launches supply zero pointer opportunities; disabled
launches remain in the intended census. Manual reads and searches retain their
excluded diagnostics in both consumers after acceptance.
`session_start` records audited intended invocations, never a complete process
launch/expectation census. G1 separately validates the complete G3 census and
independent original probe/epoch evidence before granting natural eligibility.
Without that authority, collection health remains unavailable. Report reads
never start epochs, perform tuning or ranking writes, or override authority.
The read-side snapshot/two-sweep contract detects observed drift; it adds no
writer fence or hostile same-UID guarantee. Actual installed canary acceptance
and operational H-v1 duties remain separately gated.

Per-agent memory lives under `<runtime>/orgs/<slug>/workspaces/<agent>/memory/`, one `MEM-NNN-<slug>.md` per entry. CLI: `happyranch memory list|get|search|add|update|promote|reindex`.
Runtime-owned task children automatically forward their actual invocation
session as a private CLI hint for get/search attribution. The route still
derives org, agent, and task from `SessionTracker`; nonempty explicit CLI session
IDs win, while empty flags retain the hint fallback. Stale, cross-agent, cross-org, thread/dream, or manual contexts remain
uncorrelated.

Implementation: `runtime/infrastructure/learnings_store.py` and `runtime/daemon/routes/agents.py`. Spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`.

Traps:

- `PersistentWorkspaceSetup.ensure()` never creates `memory/` when a non-empty flat `learnings.md` exists.
- `happyranch memory promote` is one-way: it replaces the body with a stub and locks the entry.

## Shared Artifacts

Keep task-only evidence in output/<actual-task-id>, report output_dir and use
supported recall. Do not reattach an already-claimed maker key to a reviewer.
For a distinct attachment handoff upload a fresh authorized copy with
`happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.


Per-org artifacts live at `<runtime>/orgs/<slug>/artifacts/`. They are opaque files produced by any agent and visible to every other agent in the same org.

Implementation: `runtime/infrastructure/artifact_store.py` and `runtime/daemon/routes/artifacts.py`. CLI: `happyranch artifacts {put,list,get}`.

Route surface: `POST /artifacts` (upload), `GET /artifacts` (list), `GET /artifacts/{name}` (download), `DELETE /artifacts/{name}` (delete). There is no update route — `POST` is an idempotent create-or-overwrite. Delete is exposed in the founder web artifacts UI only; there is **no** `happyranch artifacts delete` CLI verb.

Web UI (design-overhaul): **Flat 3-column card grid** (`web/src/features/artifacts/ArtifactsPage.tsx`). Each card shows only stored fields (`name`, `size_bytes` as a formatted size, `modified_at` as a formatted timestamp) plus Download and Delete actions. The artifact record carries **no** agent, task_id, thread, kind/type, dream_id, or PR/CI fields — so the surface renders **no** provenance badges, kind pills, status tags, PR/CI panels, or dream markers. Upload is available via a collapsible form in the page header. States: loading skeleton, calm empty ("No artifacts yet"), error with retry (`invalidateQueries` on `['artifacts', slug]`).

Traps:

- Agent access is CLI-only by design; sandboxed executors block direct writes outside the workspace.
- `artifact_put` **and** `artifact_delete` audit rows use `task_id="artifact:<name>"`; the prefix is mandatory (artifact names are user-controlled and would otherwise collide with `TASK-`/`TALK-`/`SR-` scopes).
- Org config writes (the web Settings PUT — e.g. working_hours under THR-035) emit an `org_config_write` audit row keyed `task_id="config:<section>"` (e.g. `config:working_hours`). Same mandatory scope-prefix convention for non-task actors as `artifact:<name>` above: the `config:` prefix is chosen so the value cannot collide with `TASK-`/`TALK-`/`SR-`/`JOB-` ids. Additive — no schema migration, no column change (`audit_log.task_id` already doubles as the generic scope id).
- Artifacts are blobs, not KB entries. Do not dump markdown that belongs in KB into `artifacts/`.

## Revisit

`happyranch revisit <task-id>` spawns a new root task inheriting brief and team from a terminal predecessor. Old lineage is frozen. It is TTY-gated and has no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: failed, cancelled (incl. historical founder-cancelled failed rows), escalated, in_progress/delegated (all children terminal), or completed.

**Auto-resolve forcing function (THR-018 tier #3).** When `revisit` (or a founder/manager thread-dispatch) creates a continuation whose predecessor root is `escalated` / `in_progress(delegated)`, that predecessor is auto-transitioned to the terminal `superseded` state — block_kind cleared, audit citing the new continuation root task_id (+ founder note / thread ruling). This is the maker-checker boundary: auto-resolution fires **only** because a human authorized the continuation; an un-ruled escalation with no continuation is never auto-closed. The close does **not** re-enqueue the predecessor (it would otherwise spawn a wasted manager session), but it preserves parent-wake (`_enqueue_parent_if_waiting`) so a delegated parent still learns its branch reached terminal. The delegated close is gated on **all children being terminal** and never reuses the `cancel` cascade, so live siblings are never SIGTERM'd.

Traps:

- `revisit_of_task_id` is a sideways reference, not an ancestor edge. `walk_ancestors` must not follow it.
- Per-task overrides copied to revisit roots are narrow; explicit human revisit copies only `session_timeout_seconds`.
- Auto-resolve to `superseded` must NEVER fire without a recorded successor task_id / thread ruling in the audit citation. The negative case (un-ruled escalation stays blocked) is a tested invariant.
- On the thread-dispatch path the continuation carries an optional `resolves <task_id>`, honored **only** for a manager-authorized dispatch (the founder supersedes via `revisit`). A worker self-dispatch naming `resolves` is rejected `403 thread_supersede_not_authorized` and never closes the predecessor — the maker-checker boundary, tested both directions.

## Session-Timeout Auto-Route (RETIRED — TASK-3604)

Automatic daemon successor creation on opaque agent failures (timeout, no-callback,
rate-limit, executor error, agent exception) has been **removed** per founder
direction (TASK-3604). Opaque failures now mark the task FAILED and route through
the existing recovery paths: bounded parent-wake for delegated subtasks,
escalation for root exhaustion, and explicit founder `happyranch revisit` for
human-authorized retries.

Legacy `auto_revisit_of` audit rows and the `_auto_revisit_header` / `_revisit_header_if_applicable`
readers remain for historical compatibility — existing DB rows must not become
unreadable. The `AuditLogger.log_auto_revisit_of` method is preserved for the
same reason.

Original spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md` (retired).

Traps (historical):

- The `_maybe_spawn_auto_revisit` function and its helpers (`_classify_failure_kind`,
  `_count_prior_auto_revisits_by_kind`, `_executor_failure_context`) have been removed.
- `_enqueue_parent_if_waiting` no longer receives `root_auto_revisit_spawned=True` —
  all call sites pass `False`.
- `_maybe_post_thread_followup` no longer receives `auto_revisit_spawned=True` or
  `revisit_task_id` from the opaque-failure branches — all pass `False` and
  omit `revisit_task_id`.
- Startup sweep already fails dead sessions without spawning an auto-revisit
  (THR-079 ruling); this is unchanged.

## Bounded Failure-Recovery (TASK-573 / THR-078)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior
where any child FAILED unconditionally cascade-failed the parent.

Contract (founder-approved in THR-028; refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason (`note` + completion report /
   error context) is available so the task owner can author an updated brief.

2. **Mechanical retry provenance.** A manager may re-dispatch unchanged work
   or direct revised work with a valid `revisit_of_task_id` link to a
   same-agent FAILED child under the current parent. A successor root may
   instead name the same-agent FAILED child under a predecessor root only when
   every intervening D/R/H/HT/M supersession is authenticated from its durable
   producer records within the 20-root-record / 19-edge bound. The final
   single/fanout spawn revalidates the original callback claim and lineage
   after `BEGIN IMMEDIATE`; only `Committed` enqueues children. A remote link
   cannot bypass an unresolved local failure, and no failed child is
   reparented or rewritten. The link records lineage; it does not classify a
   brief, authorize root escalation, or cause daemon retry/successor creation.
   A later COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors
   from causal selection (THR-183). No schema migration or sibling counting.

3. **Manager ownership on exhaustion.** A retried slice's second failure
   retains its durable causal lineage and wakes its owning manager. It is not
   a runtime escalation or upward cascade; a later manager-proposed escalation
   uses the configured THR-181 hook, and committed escalations remain
   human-resolved.

4. **Other child failures → bounded manager wake.** Every unresolved child
   failure preserves its causal lineage and wakes the owning manager for a
   fresh decision step. Multiple independent failures remain distinct durable
   context; they do not activate a runtime ceiling, reset, or upward route.

5. **Fan-out join context.** On a fan-out parent, per-slice terminal context
   (including the exhausted-slice trigger) is injected via
   `_inject_fanout_join_context`, giving the task owner per-slice detail.

6. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the actual decision owner back
   to the bounded-wake path. Passive pipeline carriers instead fail closed
   through their outer fanout barrier with causal-leaf context.

7. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

8. **Reviewer/QA verdict discipline.** A review/QA leg completes with a canonical verdict — review: `APPROVE | REQUEST_CHANGES | BLOCK`; QA: `PASS | REVISE | BLOCK` (plus the legacy persisted `FAIL`) — and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Traps:

- Retry links are mechanical provenance: unchanged-assignment re-execution
  and manager-directed revised work both require a valid predecessor link;
  neither is a semantic brief comparison or automatic escalation trigger.
- A manager may propose escalation through the configured THR-181 hook; a
  committed escalation remains human-resolved. The causal reason names the
  current unresolved FAILED leaf rather than a stale sibling. Non-root owners
  do not route failure upward merely because a linked retry failed.
- Chain-advance branch handles FAILED subtasks as well as COMPLETED:
  FAILED subtasks clear the chain and fall through to sibling-check +
  bounded-wake.
## Thread Broadcast Routing

Every `kind=message` thread row is a **conversational arrival** for its recipient set. Since GH-688 Phase 1 (Slice A store + Slice B route/runner wiring), arrivals coalesce: a `(thread_id, agent_name)` pair holds at most one unstarted `REPLY` (queued) and at most one running `REPLY`; a burst advances the queued/running wake's `required_through_seq` instead of minting one invocation per message. The store owns the queued/running token transitions (`record_conversational_arrival`, `claim_conversational_reply`, `settle_conversational_reply`) — routes and the runner never open-code them. Founder participates through the web UI; Feishu is not used for ongoing thread conversation. Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`; approved Phase-1 design recorded in THR-198 and TASK-5437 output.

**Thread routing is UNCONDITIONAL (founder ruling TASK-6027).** Mention routing and the strict mention-led exchange are default and permanent behavior — there is no switch, route, CLI verb, UI control, or config key for either. The shipped `threads.mention_routing_enabled` column (INTEGER NOT NULL DEFAULT 1) is an INERT legacy compatibility field: it must never be read to alter behavior, written through a product surface, or exposed on the wire (persisted true/false values cannot disable routing after upgrade); the proposed `reply_exchange_enabled` column and `org_settings.threads.reply_exchange_enabled` key were removed before shipping. **Mention routing (THR-198, Slice B) is the production routing contract:** the additive storage `thread_messages.mentions_json` (TEXT) persists a server-derived structured mention signal, and the two conversational store seams resolve the wake set at message-write time via `resolve_wake_set` (`runtime/infrastructure/thread_mentions.py`). Ratified matrix: **one/more valid current-participant mentions** → exactly that stable deduplicated set (speaker excluded); **zero valid mentions** — including no mentions, invalid/nonparticipant-only (`@founder`, typos, terminated), and self-only bodies — → full participant broadcast minus speaker. The signal is derived from `body_markdown` at write time (never client-declared; system/decline rows and pre-change history stay NULL). Routing is **write-time frozen**: a participant change after the write does not retroactively re-route already-minted wakes — the next message re-resolves against the current roster. **TASK_FOLLOWUP and BOOTSTRAP are isolated and NEVER mention-routed** (their replies keep the full broadcast). `addressed_to_json` remains unwritten/unread (its separate cleanup plan is intact). The Slice-C per-thread web control, the Slice-B founder toggle (`POST /threads/{id}/mention-routing`, audited `thread_mention_routing_changed`), and `happyranch threads mention-routing` were REMOVED by the TASK-6027 ruling — routing is unconditional; the `GET /threads*` wire no longer exposes either boolean. **Slice D (merged) ships the read-only release-measurement harness** (`runtime/infrastructure/thread_release_measurement.py`, documented in `docs/operations/gh-688-phase1-release-checklist.md` §8) reproducing the ratified acceptance metrics (G1 mentioned-message saving 293/499→~24/204, G2 founder coverage 698/0-loss, G3 org-wide decline report-only) over the Phase-2 epoch `2026-08-26T14:25:23Z` with interim labeling until the one-month window completes. **The strict mention-led exchange (TASK-5966) is unconditional** (founder-ratified THR-198 seq 194/195/196; `EXCHANGE_GRACE = 5 min`, absolute fail-open `MAX_PRIORITY_WAIT = 4 h`). A founder-authored `kind=message` with a non-empty valid mention set `P` and a non-empty deferred set `D = recipients − speaker − P` OPENS a per-thread exchange (`thread_reply_exchange`, at most one open per thread via the partial-unique index; `thread_exchange_deferrals` freezes D(E) at open). Inside the exchange: no-mention messages wake ONLY the frozen cohort `P(E)` (read from the immutable opening `mentions_json`); mention-pierce wakes the mentioned agent immediately WITHOUT joining the cohort; deferred pairs get obligation-only raises (U0 — `required` advances for EVERY recipient of every conversational message, separated from wake-mint eligibility; no token, no wake audit, watermarks stay monotonic/contiguous). Closure fires on (a) quiescence — no cohort member has a live wake whose coverage contains the exchange (running `running_through >= open_seq` or queued `required >= open_seq`; a pre-arrival claim never covers) — PLUS 5 minutes with no activity, (b) the 4-hour absolute reaper (fail-open), or (c) suppression (abort/archive/removal/corruption — no catch-up). On close/release, EVERY pair with `acknowledged < required` gets exactly ONE slot-checked catch-up wake covering the full contiguous range (the coverage-safe superset: frozen deferred set, mid-E joiners, cohort residual); a pair whose live slot GENUINELY covers the released range (running `running_through >= required`, or any queued wake — claim-time coverage is `acknowledged+1..required` and `required` is monotonic) is coalesced (never a second token); a released deferral whose live slot does NOT cover it (a pre-arrival claim with immutable `running_through < required`) is durably marked `catchup_pending` and receives exactly ONE post-slot catch-up when the blocking wake reaches a TERMINAL outcome — failed/timeout settlement, or a recoverable restart terminalization (survives daemon restart; never stranded, TASK-6057). Fail-closed recovery classifications (both-slots corruption, non-recoverable/invalid/malformed running or queued ownership) REVOKE the pending marker atomically in the same transaction and never mint a catch-up from state recovery declared untrustworthy (TASK-6065); the retained unacknowledged range is then served by the next conversational arrival (Phase-1 fail-open). `daemon_restart` is an interruption, never terminal, while a recovery replacement covering the exchange is queued/running (reconcile step 6e keeps the cohort non-quiescent). Corruption sweeps fail closed (suppress + diagnostic). **Rollback is code-version rollback / operational containment only** (TASK-6027 — the per-thread toggle, the org kill-switch, and the `exchange_disabled`/`org_exchange_disabled` close reasons were removed; the F1 measurement boundary `RATIFIED_EPOCH_F1` is PRESERVED; Phase-2 epoch/window untouched; no backfill). Reverting the code stops new exchanges; an open epoch from a reverted deploy is closed by the normal idempotent closure bounds (quiescence + 5-min grace or the 4h fail-open reaper; startup reconcile 6e and the write seams evaluate the same bounds) — never stranded. Any-burst deferral and the substantive-peer-engagement doctrine amendment remain separately gated. **Observation remains pending — a shipped diagnostic is not proof of the population outcome.**

Traps:

- Broadcast is unconditional; declines are silent.
- Decline-by-default doctrine is prompt-injected for `REPLY`, not in `runtime/skills/bundled/thread/SKILL.md`.
- Agent replies no longer enforce a hard `turn_cap` ceiling; turn count is still tracked and displayed but cap enforcement was removed per THR-046.
- **Coalescing (GH-688 Phase 1).** A burst creates at most one unstarted `REPLY` per pair; new queue tokens are enqueued only after the arrival transaction commits. An arrival while queued or running only advances `required_through_seq`. A successful reply/decline acknowledges **only the claimed coverage** (the immutable `running_through_seq` snapshot at claim); arrivals during the run yield exactly one post-settlement follow-on. Failure/timeout never hot-loop: the range stays unacknowledged, projects `retry_required`, and the next conversational arrival covers the retained plus new range.
- **Runner claim (GH-688 Phase 1).** A conversational `REPLY` must pass the durable queued→running CAS (`claim_conversational_reply`) before any prompt materialization or provider work; a stale/duplicate queue notification no-ops there. The per-`(thread, agent)` in-memory lock remains for process-local serialization only — it is not the durability mechanism. The prompt explicitly states the claimed inclusive `[running_from_seq, running_through_seq]` range and renders each required message in order.
- **Settlement is exactly-once.** Route reply/decline and every runner terminal path (clean-no-callback, provider failure, timeout, materialization failure, runner crash) settle through the store. Abort/archive/participant-removal discard through an explicit boundary (`discard_reply_delivery`); discarded wakes never resurrect and a later message starts after the boundary.
- **Startup recovery (GH-688 Phase 1).** `_sweep_on_startup` replaces only the conversational `REPLY` portion of the generic reaper: a valid queued wake — a pending, **unstarted** same-pair `REPLY` (the claim CAS enforces the same precondition) — is retained and re-enqueued; an interrupted running `REPLY` is terminalized once as `daemon_restart` and replaced by exactly one queued wake. A malformed queued slot referencing a **started** receipt fails closed: the owned pending `REPLY` receipts for the pair are retired with `invalid_queued_started_on_recovery`, the slot clears, nothing is re-enqueued, and the preserved `required_through_seq` lets the next conversational arrival mint the single covering wake. `BOOTSTRAP` and `TASK_FOLLOWUP` keep the generic `daemon_restart` reaping unchanged.
- **Wire contract (GH-688 Phase 1).** `GET /threads/{id}` and `GET /threads/{id}/messages` carry a pair-level `reply_delivery` projection (running | queued | held | retry_required, inclusive `from_seq`/`through_seq`, store-computed `coalesced_message_count`, `started_at`, `last_terminal_reason`). Precedence is running > queued > held > retry_required > settled omission. Held requires BOTH an OPEN `thread_reply_exchange` and the participant's matching HELD `thread_exchange_deferrals` row; released/suppressed/closed/missing-half history never masks a genuine retry. Terminal reason is historical metadata, exposed only on a genuine current retry and never classification proof or fault copy for held. The projection is never fabricated from per-message rows and never claims a subprocess exists beyond `started_at`. Historical per-message `responder_status` strips are unchanged in shape and now ALSO carry the authoritative invocation `purpose` (`reply` | `task_followup`; BOOTSTRAP stays excluded from the grouped query) so wire classification never has to infer purpose from the triggering row kind — a coalesced REPLY delivery range can anchor on a SYSTEM row (the follow-on mint keys the first unacknowledged sequence, which may be a system divider).
- **UI presentation (GH-688 Phase 1 Slice C).** The thread detail rail shows a compact "Reply delivery" section driven ONLY by the store projection: current `running` pairs stay individually visible first with full wrapping agent identities; `queued` pairs are visually distinct and grouped behind a native keyboard/screen-reader disclosure with truthful singular/plural counts; `held` stays visible as healthy neutral "waiting for current exchange" and is never typing, an active subprocess, or a fault; `retry_required` stays visible as an amber diagnostic. Detail captions preserve the inclusive range: queued reads "N message(s) coalesced · message(s) F–T" (static — never an active subprocess), running reads "replying · messages F–T" (the only subprocess evidence is `started_at`), held reads "waiting for current exchange · messages F–T", and retry-required reads "retry required · messages F–T" plus at most one bounded short caption derived from the required nullable `current_failure_category` (`no callback`, `no callback after reprompt`, or `infra fail`). Compact consumers never render or derive copy from raw `last_terminal_reason`; raw terminal detail remains confined to the existing detail-wire/audit surfaces. Fully settled pairs remain absent from this live store projection; their truthful terminal evidence remains in the per-message responder history rather than being recreated in the rail. The transcript tail's live indicator (TypingBubble) is driven only by queued/running pair projection; held and retry_required remain rail-only. Inferred per-message rows remain only for special-purpose wakes (BOOTSTRAP / TASK_FOLLOWUP) that are intentionally outside `reply_delivery` — preserved even when the same agent concurrently holds a conversational REPLY pair. Inferred-row suppression is purpose-aware (the wire `purpose`, never the triggering row's kind): a REPLY whose coalesced range anchors on a system row still reads `purpose=reply` and is masked by its pair row — exactly one replying bubble — while a same-agent TASK_FOLLOWUP stays visible (never agent-name-only). Per-message terminal responder history is unchanged (and now also renders under SYSTEM rows, so a settled system-row-anchored REPLY range shows its terminal `replied` marker). SSE tail events and the send/invite/remove/archive/resume/abort mutations invalidate the thread-detail query so the projection stays fresh — no new wire events were added.
- **Audit lifecycle (GH-688 Phase 1 Slice C).** The six approved actions — `thread_reply_wake_created`, `thread_reply_wake_coalesced`, `thread_reply_wake_claimed`, `thread_reply_wake_settled`, `thread_reply_wake_cancelled`, `thread_reply_wake_recovered` — are emitted ATOMICALLY inside the store transitions (`record_conversational_arrival`, `claim_conversational_reply`, `settle_conversational_reply`, `discard_reply_delivery`, `recover_reply_delivery_state`) using the existing `audit_log.task_id = THR-*` scope convention. Duplicate queue notifications and idempotent recovery can never fabricate events (stale claim CAS no-ops, `seq <= required` idempotent arrivals, and pure slot-clears emit nothing). Payloads carry only truthfully observed fields — agent, inclusive range, 8-char token prefix, outcome/reason/follow-on — never full single-use tokens.
- **Strict exchange (TASK-5966).** The exchange-epoch gate suppresses every deferred-pair wake mint for the duration of the exchange (no-pierce property — the only pierce sources are a mention of the agent, a pre-existing queued wake claimed mid-exchange, and TASK_FOLLOWUP/BOOTSTRAP isolated broadcasts) and mints exactly ONE slot-checked catch-up per pair at closure covering `acknowledged+1..required`. The settled audit payload is extended (`covered_from_seq/covered_through_seq/claimed_at/minted_at/exchange_held`) so tightened D1 attribution is durable: a wake claimed before a message (`claimed_at < M.created_at`, `running_through < M.seq`) can never be its covering substantive reply; a queued settlement attributes only messages minted-at-or-before the wake. Closure is an atomic CAS (`open → released|suppressed`); duplicate evaluations are silent misses (no audit, no double catch-up). Abort/archive suppress the exchange with no catch-up; participant removal suppresses the removed member's deferral rows and the removed pair never receives a catch-up. The reaper (30s tick, `runtime/daemon/exchange_reaper.py`) enforces the 4h fail-open; startup reconcile (step 6e) runs the corruption sweep (5 fail-closed classes) then idempotent closure evaluation. The exchange is UNCONDITIONAL (TASK-6027): there is no disable path — an open epoch is closed only by the ratified bounds (quiescence + grace, 4h fail-open, or suppression), and code-version rollback is the only operational containment.

## Thread Agent-Session Resume

Failed thread turns persist additive, capped ``stdout_tail`` and ``stderr_tail``
audit diagnostics. The displayed reason may prefer a structured Claude terminal
failure only when stderr consists solely of known workspace-trust warning lines;
task failure notes also name the proven Claude API-error/session-limit result
shape while retaining its bounded reset notice on existing task/thread/dream
surfaces. This is not a comprehensive redaction guarantee. Meaningful stderr, including trust-warning
lookalikes, remains the human cause; the structured reason/reset notice is
supplementary when stderr is absent or exclusively exact known-benign lines.
The raw tails still drive exact
eviction and other classifiers. Session-limit terminal output is not a
short-backoff automatic retry signal.

Claude-backed thread participants reuse their Claude session across turns. State lives on `thread_participants.agent_session_id` and `last_resumed_seq`. Plan: `docs/superpowers/plans/2026-06-02-thread-claude-session-resume.md`.

Implementation: `runtime/daemon/thread_runner.py`, `runtime/orchestrator/executors.py`, `runtime/infrastructure/database.py`, and `runtime/infrastructure/audit_logger.py`.


Traps:

- Optimization, never a correctness dependency — for claude/codex/pi only.
- `last_resumed_seq` advances only after a successful subprocess.
- Per-`(thread, agent)` `asyncio.Lock` protects read-run-update.
- **Eviction invalidation is transactional (THR-200).** On a provider-declared
  session-not-found, the `agent_session_evicted_fallback` audit and the durable
  `agent_session_id = NULL` invalidation commit in ONE transaction BEFORE the
  full-prompt fallback; a failed fallback leaves the id NULL and the delivery
  watermark unadvanced. Classified ONLY from the executor that ran, reading
  ONLY the proven return code and stream, and REQUIRING the anchored
  provider-declared signature bound to the regex-escaped attempted session id
  — claude `No conversation found with session ID: <attempted-id>` (rc=1
  stderr), codex `Error: thread/resume: thread/resume failed: no rollout
  found for thread id <attempted-id> (code -32600)` (rc=1 stderr — the
  observed COMPLETE line including the CLI envelope; only horizontal
  whitespace may pad it), pi `No session found matching
  '<attempted-id>'` (rc=1 stderr) — each verified 2026-08-28 to echo the
  attempted id verbatim; opencode 1.18.25 is the exception — rc=1 + EMPTY
  stdout + one complete LF/CRLF-delimited physical stderr line exactly
  `Error: Session not found` after ANSI-SGR stripping (the id is NOT echoed;
  unrelated physical lines may coexist, but same-line prefix/suffix and
  cross-line assembly never match; no global auth/quota/transport token veto
  is applied). Generic legacy substrings, cross-provider text,
  stdout-only text, wrong rc, wrong/missing id, prefix/suffix near-matches,
  a marker embedded in auth/quota/transport output, and generic failure /
  auth / quota / transport / ambiguous output never trigger the fresh retry.
- **Resume eligibility (<=0 watermark).** A stored provider id whose
  `last_resumed_seq` is null/zero/negative (<= 0) is never eligible for
  resume — the runner must make a fresh invocation with the complete
  canonical transcript; resume requires a strictly positive delivered
  frontier (plus the full contiguity proof).
- **Lifecycle invalidation (THR-200).** Archive, successful executor switch, and
  agent termination clear resume state (id NULL, watermark 0). Each boundary is
  a database-owned transaction: the participant reset and its
  `thread_session_invalidated` audit commit atomically (termination runs
  reset+audit inside the existing terminate-cleanup transaction; archive wraps
  admission, delivery discard/BOOTSTRAP reaping, breaker/exchange suppression,
  status flip, every participant reset, native audits and the archive message
  in one transaction).
  A reset/audit failure leaves no partial lifecycle state — a failed switch is
  rolled back (no new executor installed) and a failed archive leaves the
  thread OPEN with every session row unmodified. Participant removal deletes
  the row and its session state together (no redundant clear).
- **No fingerprint invalidation.** The proven codex/pi contracts resume across
  model/config changes, so no executor/model/config fingerprint column is added.
  EXCEPTION: opencode sessions are bound to their project directory — resume
  is safe only for the identical workspace path; org relocation / workspace
  path changes must invalidate stored opencode session ids (schema-free;
  relocation-time sweep is a separate founder-gated follow-up).
- **Equality self-heals (THR-200).** `last_resumed_seq == running_from_seq` runs
  the full prompt (never omits a required sequence) and recovers after one
  successfully settled full-prompt turn — no watermark-comparison change.
- **Transport (THR-200).** Claude/Pi/Codex/OpenCode deliver the prompt on stdin
  argv-based with a pre-spawn `prompt_transport_too_large` guard. Encoded
  byte size is transport-only, never a cost/reset policy.
- **pi uses `--session`, never `--session-id`.** `--session` fails when the id
  is missing (the eviction signature); `--session-id` would silently CREATE a
  fresh session and omit transcript messages.
- **TASK execution never resumes provider sessions.** Only
  `thread_runner.run_invocation` passes `resume_session_id`; the orchestrator's
  task path and wake/dream runners always launch fresh. Pinned by
  `tests/test_thread_resume_parity.py`.
- `ExecutorResult.agent_session_id` is not `ExecutorResult.session_id`.
- **GH-688 Phase 1 claim gate + no-message-omission proof.** For a claimed conversational `REPLY`, a resumed session may use the delta only when the stored watermark is strictly below the claim's `running_from_seq` AND the ENTIRE required post-watermark range is proven present and contiguous at the production seam: the runner loads the canonical transcript UNCAPPED, independently queries the authoritative transcript max, and authorizes the delta only when every required claimed sequence exists (the claim's inclusive end must also exist). Truncated loads, internal holes, equal/ahead watermarks, a null/zero/negative (<= 0) watermark (a stored id with watermark <= 0 is never eligible), and claim ends beyond the transcript fail closed to the genuinely complete canonical full prompt — a delta can never omit a message the delivery state requires. `last_resumed_seq` is observed session presentation and is never the delivery cursor.

**Cleanup indexes and breaker scheduling (issue1019 / THR-295).** The common
schema tail creates three nonunique indexes with `IF NOT EXISTS` after legacy
columns exist: tasks(assigned_agent,created_at DESC,id DESC), audit_log(task_id,agent)
where action='workspace_cleanup_triggered', and task_results(task_id,agent,id DESC).
Existing names are retained without definition validation; display SQL is unchanged.
Delivery listing and mint run sequentially via `await asyncio.to_thread` under the
original DB RLock. Close uses that lock too. Removal can cause a logged sweep error;
cancellation can leave worker work running. Pending-token recovery, publication,
claim/count-once and cooldown behavior remain unchanged. No org lifetime lock or
worker drain is added; other synchronous DB callers retain their limitations.

## Thread Task Followup

When a task dispatched from a thread reaches true terminal state, `_maybe_post_thread_followup` appends a system message and mints a `TASK_FOLLOWUP` invocation. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

Traps:

- Helper runs after task failure is finalized (no auto-revisit spawns; the task is already terminal FAILED).
- Only root tasks fire followups.
- Dispatcher identity comes from the `task_dispatched` audit row.
- Cross-thread enqueue uses `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)`.
- Terminal gate is completion/failed **plus** `superseded` (completion-class → `task_completed` kind). A thread-originated task auto-resolved by a continuation must still emit its followup; missing this terminal silently drops the superseded state from the thread lifecycle.
- **GH-688 Phase 1 isolation.** `TASK_FOLLOWUP` is a causal one-shot direct mint (`mint_followup_invocation_with_cap_extend`), never coalesced into reply delivery state and never routed through the claim/settle surface — even with a reply backlog on the same pair, the followup fires as its own `TASK_FOLLOWUP` row.
- **Daemon lifespan ordering (THR-109).** `_attach_thread_queue_wiring` must run before `ensure_workers_started` so the orchestrator's `_thread_queue` and `_main_loop` references are populated before any task worker can execute a step. Without this ordering, a rapid terminal task fires `_append_followup_system_and_reinvoke` while those references are still `None`, producing `enqueue_unavailable` and stranding the invocation as permanently pending. The blocked-on-job recovery after wiring still has a wired queue (THR-109 PR).

## Dreams

Dreams are private scheduled reflection runs, separate from tasks and threads. Per-org config lives under `dreaming:` in `<runtime>/orgs/<slug>/org/config.yaml`. A dream may write per-agent learnings, persist KB candidates, and create a founder-only thread when there is meaningful output.

Traps:

- Dreams are not `TaskRecord`s and must not appear in task metrics.
- Dreams produce KB candidates, not KB entries.
- Startup catch-up runs at most today's missed dream; it does not replay every missed day.
- Failed or timed-out dreams do not advance the next input window.

## Thread Dispatch Self-Only Rule

`/threads/{id}/dispatch` rejects calls where `effective_target != dispatcher`. Spec: `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

Traps:

- Applies to task owners and subtask agents uniformly.
- Doctrine is system-prompt-injected through `_thread_talk_dispatch_doctrine_section()`.
- Shared error hint `SELF_DISPATCH_HINT` lives in `runtime/daemon/routes/_doctrine.py`.

## Thread Composer Attachments

The web thread composer (`web/src/design-system/patterns/Composer.tsx`), the thread detail page
(`web/src/features/threads/ThreadsPage.tsx`) and the new-thread dialog
(`web/src/shared/threads/NewThreadDialog.tsx`) upload each selected file to the org-shared
`POST /api/v1/orgs/{slug}/artifacts` (multipart) **before** sending/composing, then pass the returned
`artifact_name` refs on `POST /threads/{id}/send` (JSON) or `POST /threads`. The CLI/agent thread-scoped
`/threads/{id}/attachments` store is unchanged; the browser keeps using shared artifacts.

Traps:

- Each selection carries a stable, non-metadata id (`createSelectionIdFactory`). Two identical `File`
  objects (or the same File selected twice) are distinct selections with distinct chip keys, and removing
  one never removes the other.
- A partially failed attempt retains each already-uploaded selection's ref by selection id, so a retry
  re-uploads only the selections without a ref and preserves the send order. Removing a failed or
  completed selection invalidates only that selection's cached ref. Each submission reads a run-owned
  snapshot of those ref/name caches, so a departed run cannot observe a replacement view's selections
  even though `Composer` selection ids restart at `sel-1` on remount.
- Each selection's artifact name is reserved once (`allocateArtifactName`) and reused verbatim, and the
  page never reuses a name it has already allocated. The allocator searches upward past the former
  1000-index ceiling until it finds a free candidate, so it never returns an occupied name; without this, a
  same-second retry could regenerate a retained name and `ArtifactStore.put` would replace the retained
  artifact's bytes.
- `Composer.submit` has a synchronous in-flight latch and the pages own the run's pending presentation, so
  a double-click / Enter+Send during a held upload produces exactly one submission and disables
  attach/send/remove until it settles. The latch is released on failure and reset when the destination
  changes, so a submission left in flight by a departed view cannot block the replacement view's first
  submit. Pending is the run's own state, not the surviving mutation observer's `isPending`, so a held send
  (or compose) in a departed org/thread — including the same thread id in another org — cannot disable the
  replacement view.
- Each submission captures its destination `(orgSlug, threadId/dialog, payload)` at first submit; uploads,
  the send and the new-thread compose use that snapshot, and late results only mutate the originating view
  while it is still mounted and active. The destination is an ORG-scoped generation, not just a thread id:
  an org switch that keeps the same thread id, an A→B→A return to the same thread, and a full unmount are
  all departures, so a stale success cannot clear the replacement view's draft, chips, error, latch or
  dialog and cannot retarget an in-flight compose.
- Failures surface on the existing single error line. Attachment/artifact error codes are mapped in
  `web/src/lib/threadErrors.ts`, and a confirmed send failure is never labelled as the last file's upload
  failure.
- Caps are unchanged: `MAX_THREAD_ATTACHMENTS = 5` (`runtime/daemon/routes/threads.py`) and the 10 MiB
  artifact cap (`runtime/infrastructure/artifact_store.py`). No drag-and-drop or size/state chip was added.

## Jobs

Per-org jobs use a SQLite table and files at `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`.

Implementation: `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`, `runtime/infrastructure/database.py`, and `runtime/infrastructure/audit_logger.py`.

Routes under `/api/v1/orgs/{slug}/jobs/`: `POST /submit`, `GET /`, `GET /{id}`, `POST /{id}/run`, `POST /{id}/reject`, `GET /{id}/output`, and `GET /{id}/events`.

Traps:

- Agent identity derives from auth context, never payload `agent`.
- Submit auth path: `(task_id + session_id)`.
- `review_required` and `persistent` are honor-system on submit.
- Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks.
- Graceful daemon teardown stops task-queue admission before signalling jobs.
  A job actually signalled there persists `failed/daemon_shutdown` with its
  real exit code; a natural pre-signal exit keeps its natural result and
  `task_ended` has precedence. Non-JSON `jobs show` prints a non-null reason.

## Task Blocked By Job

Blocked-on-job tasks use `tasks.blocked_on_job_ids` plus `BlockKind.BLOCKED_ON_JOB`. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`.

Implementation touches `runtime/orchestrator/run_step.py`, `runtime/daemon/jobs_runner.py`, and `runtime/daemon/app.py`.

Traps:

- State transitions are owned by `run_step_impl`, not the route or resume helper.
- Three resume callers must stay symmetric: job terminal hook, immediate block branch check, and startup recovery.
- Predicate is all-terminal, not any-terminal.
- `metadata` is a function parameter, not shared state.
- Shutdown leaves the task and session parked. Branch 3 and the lifespan scan
  converge through `enqueue_if_absent`; the resume header renders status plus
  available reason/exit details without changing audit `job_outcomes` values.

## PR CI Wait / Guarded Merge

Traps:

- SHA-pin every wait. If the PR head changes, stop with `stale_head`; do not continue polling the new head silently.
- "No checks" is not pass. Use required contexts when branch protection provides them, otherwise require explicit expected checks and a bounded settle window.
- GitHub `mergeable` / `mergeStateStatus` is not CI green. It is one guard in addition to CI pass.
- Do not use `gh pr merge --auto` as proof of safety while required checks are absent on `main`.
- Merge completion is allowed only through the guarded helper or founder-reviewed job, not broad worker `gh pr merge` allow-rules.
- The poll job entrypoint is `python -m runtime.daemon.pr_ci_waiter --repo ... --pr N --head-sha <sha> --expected-check ...`; the merge entrypoint is `python -m runtime.daemon.pr_ci_merge --org ... --repo ... --pr N --head-sha <sha> --merge-method ... --ci-verdict ... --review-task-id ... --qa-task-id ...`. Both print structured JSON to stdout and exit with mapped codes.

## Reply-delivery compact retry captions

The live `reply_delivery` wire projection carries a bounded
`current_failure_category` (`no_callback`, `no_callback_after_reprompt`, or
`infra_fail`) only for a genuine current `retry_required` obligation. Both
compact consumers render that short category and never raw
`last_terminal_reason`; the raw reason remains available to existing detail
and audit surfaces. Queued, running, held, replaced, and settled obligations
have no current failure category, preventing terminal history from labeling a
later obligation.

## Feishu Notifications (REMOVED)

Feishu was removed in TASK-302 (THR-022). The web UI and threads are the sole control path for dispatch / revisit / resolve-escalation. Legacy `feishu_notifications` config blocks are tolerated on load but ignored. Database correlation tables (`escalation_notifications`, `processed_event_ids`) remain dormant in place.

## Retired autonomous thread continuation

Both served resolution contracts — `POST /tasks/{task_id}/resolve-escalation`
and `POST /threads/{thread_id}/resolve-escalation` — return the stable `410`
error code `retired_autonomous_continuation` when a former THR-166 envelope is
present. Presence includes null, empty, malformed, and otherwise valid values
for `policy_id`, `policy_version`, `policy_provenance`,
`continuation_class`, `attestation_checks`, or `evidence`; the task route also
rejects the former autonomous identity markers `invocation_token` and
`dispatcher`. Rejection happens before actor fallback, invocation consumption,
or the shared human resolver. A field-free agent thread `continue` is also
retired. Ordinary human task resolution and thread `supersede` remain
unchanged.

Complete legacy release references also cover independently pinned v0 and
historical authority-v2 constructor inputs, including the organic additive
migration order. Each reference runs current generic migrations and the
requested F/E/G installer on its own disposable database; the comparison
includes every SQL object. A candidate never supplies a baseline, and any
required reference failure or unrelated object still refuses. This changes
no observed-only authority-v2 claim rule.


## Root task Pause and Resume (THR-292)

Transient child_wait observations preserve stored lifecycle and exact report
identity; completed reports on in_progress rows do not rewrite those rows or
prove consumed results. Capped/unread evidence is unavailable, not empty.
Detail unknown/job blockers say descendant cleanup unconfirmed; no terminal-job,
exit0 or absent-PID heuristic discharges them. The future job-owned closure
producer is excluded from TASK-11437, and historical unknowns stay fail-closed.
Seven fanout row categories and genuine prior audit attribution never infer
running/queued/succeeded or parent resumed/completed. Backend/protected proof
remains unexecuted; synthetic frontend observations establish only that boundary.


Task Pause is a separate root control with generation-checked, idempotent toggles. New Pending-root Pause is409; terminal lifecycle wins. Pausing means committed work or unavailable drain evidence remains; Paused requires complete absence of blockers. A callback, PID0 placeholder, persisted PID or passthrough CLEAN receipt cannot prove tree quiescence. Committed work can settle while held, but future steps/retries/job approvals and founder origin Continue/supersede/revisit/dispatch(resolves) are gated. Only a captured ordinary-purpose actually running session (including a normal v2-admitted manager session) retains its unreviewed auto-job exception. Cancel, job reject/stop and genuine result settlement remain available. Another accepted manager successor root remains unheld. Overview is bounded/read-only and never safe-to-restart. [Current contract](../superpowers/specs/2026-10-10-root-task-pause-resume.md).

Unclaimed completion recovery retains the actual original return and SIDs while held, with no running deadline. Its 120-second deadline is pinned atomically with the accepted real admission/claim, binding and launch journal. Preclaim/admission waiting spends nothing; claimed recovery retains its expiry across Resume, duplicates, retry and restart, including committed no-launch failure. Unresolved original host/tree evidence is separately retained under the original session/owner/generation before recovery conversion. It remains an unknown drain blocker after callbacks, terminal lifecycle, restart or quiescent recovery, without owning a new producer. Known-quiescent originals add no unknown blocker, and deferred unclaimed recovery alone is not execution.

## Functional naming UI and API contract

The existing org surface has an independent human-founder name editor. Active
agent detail, pending enrollment and terminated identity surfaces expose agent
name editors, keeping immutable ID and definition settings separate. Editable
names use exact ASCII 1–64 grammar (first letter/digit, then letters/digits/_/-),
without trimming. PUT sends only `addressable_name` and positive
`expected_name_revision`. A stale conflict performs authoritative GET, retains
the draft and requires explicit resubmission. An ambiguous response reads back
and reports the observed name/revision or uncertainty, with no blind retry or
claim that failed transport rolled back a commit. Absent/unavailable metadata
cannot rename. A displayed human label provides no authentication proof.

Provider-aware naming hooks share one org-scoped identity list/query. Successful
rename/readback refreshes identity metadata, preserving canonical IDs, settings
and unrelated drafts. Current labels display Name · ID (ID alone when equal or
unavailable); historical IDs, links, keys, owner/filter/recipient values and
stored message text remain canonical/literal. Metadata follows existing focus/
reconnect behavior and bounded 30-second polling; availability is never a global
gate for established ID operations. Editor drafts are scoped by org/kind/ID and
survive locale changes and identity refetches.

Real thread compose/follow-up/invite/forward and task read-filter pickers search
current names and permanent IDs. Selected values stay IDs across renames. Clear
invalidates pending task-filter resolution; stale success/error/finalization cannot
reapply a cleared filter, show an obsolete alert or release a newer attempt. Founder
is a separate human option routed as established `@founder`, never an agent
participant or task owner. There is no task-create owner GUI in the present web
source; the existing task agent filter is the relevant selector. Prospective
invite resolve omits `thread_id` because that optional scope means existing
participants; the actual invite endpoint retains final action eligibility.
Recognized former body/recipient tokens stop UI-managed associated attachment
uploads when caught by separate preflight. Unknown body @text remains literal,
with ordinary quotes eligible and email/code/escaped tokens excluded. This read
is not a
reservation: final server admission may reject after an intervening rename, and
already-completed uploads are not claimed rolled back. New en/zh-CN text uses
existing controls/tokens. The naming browser adapter captures 390×844/1440×900
light/dark states in en/zh-CN. Source-pinned captures and interaction receipts are
maker evidence; they do not establish pixel fidelity or independent acceptance.

The four existing naming adapters mirror IdentityView/ResolveResponse/RenameBody.
The repository contract snapshot is a summarized route/parameter/status artifact,
not full JSON Schema. Its four entries were originally authored from source;
the supported standalone generator and genuine served-schema comparison verify
that summary independently. Source-pinned schema/frontend/browser receipts live
in the maker handoff; final-candidate independent review and behavioral QA remain
separate requirements. The shipping-page MSW assertions in
`web/src/test/identity-names.test.tsx` add UI portions of scenarios2/3/4/5/8/10;
API assertions are `web/src/lib/api/identities.test.ts`; scenario9 also exercises
canonical skill-row attributes and stable row identity during label refresh.
These provider/component tests use frontend mocks. Source-specific commands,
red/keeper results and actual execution status belong in the maker handoff;
frontend results do not establish daemon/DB end-to-end behavior. Existing
twelve-scenario integration bodies/fixtures are preserved. Python units remain RETIRED under THR-291 seq40;
general integration remains SKIPPED THR243seq42. Published source and maker verification do not establish independent acceptance,
merge, deployment or whole-feature completion.


Naming verification source now has the explicit finite selection
`tests/helpers/identity_names/run.sh` for the same twelve THR293seq34 business
scenarios. Core/ASGI assertions retain their layer attribution; naming-only
`test_identity_names_e2e.py` and `web/scripts/identity-names-e2e.mjs` add real
served daemon/CLI/native callback/browser-to-SQL assertions for2/3/6/8/9 and
real390x844/1440x900 en/zh-CN light/dark captures. Source-pinned hosted receipts
record actual daemon/browser outcomes separately from MSW/component evidence. The
current case record, mutations and unselected historical branches are in
`docs/superpowers/specs/2026-10-09-identity-names-core.md` under Current finite
naming verification source. Standalone OpenAPI --check and a genuine served
schema comparison are both retained in the hosted receipts. Clean hooked
publication precedes each authorized disposable GitHub naming operation. The
naming-only workflow and `scripts/identity_names_hosted.sh` are published on the
open naming PR; each corrected source requires its own attributed verification. The
runner preserves source/evidence sentinels through init/prepare/work/export with
a bounded offline mount holder, refuses output truncation, and reserves cleanup
under one job-start deadline. Python unit retirement and
general integration exception retain their exact scope; no behavioral acceptance,
merge, live migration or deployment is established.


## THR299 exact lineage receipt

The [closed reconciliation contract](orchestrator-contracts.md#exact-failed-continuation-reconciliation-thr299)
adds one current-time authenticated audit linking FAILED10343 to existing10554,
retaining10565 and older10319, solely for ordinary TASK10725 in happyranch /
engineering / engineering_manager. Original FAILED children10450/10329,
parents/results/statuses/timestamps/retry accounting and old producer evidence
remain preserved. Exact replay adds no row; immutable historical authority is
validated from original session/dispatch evidence independently of current
owner liveness. Fresh state/holds and current retry validity are separate.
No arbitrary pair, grant, schema migration, task allocation or queue side effect
is added. The verifier keeps20 roots/19 edges, same-agent FAILED-child checks,
local-failure priority and writer-time final-spawn fences. Reads are nonmutating
and advisory. Synthetic exact-positive/dependent proof remains UNPROVEN under
Founder299seq11; reviewed source is distinct from installation/reconciliation
and TASK10565's naming acceptance.


### Current owner review discovery (THR139 seq506)

GET `/api/v1/orgs/{slug}/workflows/activations/{activation_id}/review-selection?proof_kind=owner-human`
returns closed `workflow-owner-review-selection@1`: activation/instance IDs, nullable
original `{handoff_id,human_request_id,collection}`, nullable successor
`{revision_operation_id,human_request_id}`, and `return` `{eligible,reason,required_action}`.
The collection has the existing closed RevisionCollection fields, including current
validity and approved-use status. No H yet gives null original; no authenticated graph
gives null request/collection; a template with no human slot has null human ID.
A genuine successor is discoverable immediately at admission, before r2 staging.
IDs navigate independently authenticated readers and grant no access or approval.

Relational same-org/current activation/current owner and active read grants precede
private proof decoding, repeat after each awaited lock and at the coherent read
boundary. Complete H/I, pinned contract and template-selected human request closure
validate before disclosure. No peer IDs/findings/inputs, bytes, sessions or proofs
are returned. The GET reuses READ-ONLY `current_collection`; no domain DB, authority
lease, event, queue or join writes. It never collects, discovers or fulfills work.

Return readiness reuses the full read-only predicate consumed inside POST's atomic
writer: supported @3/@4 document contract/actions and I, full current authority,
a genuine successful Boolean-quiescent r1 source, all peers closed with an objection
and no join, no successor, same author and available capacity. A needs_revision
label alone is insufficient. GET advises only; POST retains replay/CAS/current
revalidation and allocation ordering. Access loss refuses the whole read; visible
effect holds return eligible:false with existing reason/action. Missing proof403,
invisible/wrong-owner404, malformed/duplicate/unknown/blank selectors, IDs or any
nonempty GET body422, missing compatible H/I409 with operator remedy, storage500
category-only. No migration is performed.

The typed `getOwnerReviewSelection` client sends only owner-human. Older daemons
report discovery unavailable; dependent actions stay disabled without fallback
access or guessed IDs. Native route classification/assertion parity preserves the
existing SurfaceAllowList and credential/transport policy; source inspection is
not native execution evidence. Mounted UI is a separate serial unit. Python units
remain retired, integration SKIPPED24342, broad behavior/proof UNEXECUTED and
FOUNDER-WAIVED139429/430; lint/static evidence is not behavioral acceptance.

Naming namespace capture includes active/pending executable agent managers and
workers. Mainline typed human Founder managers have no agent definition and are
excluded from that agent set; Founder remains the separate human naming owner.
The combined I/roster/naming behavior remains unverified; see the current naming spec.


## Disabled coding release catalog — K0

K0 is a release catalog, always disabled. The pure reader accepts only the fixed
634-byte canonical `workflow-coding-definition@1` preimage (SHA256
`6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`), rejecting
missing/duplicate/unknown keys, scalar type substitutions, bounds violations,
nonfinite/BOM/noncanonical bytes and any changed pin. It uses only the constant
package resource, never DB/defaults or a configurable path. Projection and GET
perform no workflow writes, template import, task allocation or readiness lookup.
`enabled:false,reason:release_catalog_only` cannot become runnable from catalog
bytes, labels, hashes or successful reads. Existing document/code-admission refusal
and all historical families retain their meaning.

See [the current ordered-coding K0 contract](../superpowers/specs/2026-10-14-ordered-coding-workflow.md).
Independent final-head source review/scoped QA and lint/static checks remain
separate gates. Python units RETIRED; integration including callback SKIPPED24342;
behavioral/negative/mutation/repetition/protected proof UNEXECUTED /
FOUNDER-WAIVED139429/430. No package/native build or ad hoc daily E2E is authorized.

## Bounded repository artifact source — K1

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
See [the current K0/K1 contract](../superpowers/specs/2026-10-14-ordered-coding-workflow.md).

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
match the source tuple and full closed workflow_start command/INITIAL work radius, never
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
