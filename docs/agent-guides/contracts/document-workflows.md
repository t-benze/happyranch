# Workflow foundation and document-review contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

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


**Retained artifact foundation A — THR139seq480/477, ROOT TASK10693.** Pure frozen
`artifacts.ArtifactRevision` separates org/instance/stable output slot from a
stored submission ID, strict positive revision, document/code-change subject and
authenticated provenance. Current document integrity and both receipt subject
checks use it; legacy scope/brief wrappers preserve exact historical bytes,
IDs, digest preimages and error/admission ordering. No store/schema/graph import,
I/O or recursive validation enters the pure adapter. Closed canonical code-change
descriptors pin repository/object-format/full head/optional base and navigation,
but mount no repository reader or template admission. Product-design and the
one-human proposal share the document adapter; code shares the representation.
B2 repository resolution and C r2/fresh sign-offs/reassignment/recovery/UI,
Founder465 public/revision/real UI acceptance and Phase2 ordered coding/CI/merge
adapters remain outstanding. Seq493 approves seq475 staging as described above; H revision1/unique intent and @1/@2/@3 history remain unchanged.
Behavior/proof UNEXECUTED/FOUNDER-WAIVED139429/430; integration SKIPPED24342;
Python units RETIRED. Full independent review/scoped QA renew at final head.
Exact grammar: [Features And Invariants](../../agent-guides/features-and-invariants.md).

**U3b(a/b/c/d) dependency-incomplete checkpoint (THR139 395/410/445/457/465).**
Internal F5 staging, genuine successful/quiescent agent receipts and authenticated
human receipts retain matched closed readers and terminal/reaper consumers.
Both receipt writers now collect ALL configured current template slots. Partial
responses preserve peers/findings. All responses with a declared objection close
the round while the instance remains reviewing and derive needs_revision/owner
revision action; no joined event or automatic r2. All approvals with current
approve-planning-input authority atomically close the round/instance and append
one immutable service:workflow_review_join event/replay, with full grant/pins and
actual receipt/result/assignment identities. Cold discovery reuses the existing
service; ordinary holds do not starve later work, corruption remains visible.
Historical joins grant no fresh approved use; current source/authority holds are
reported separately. No extra author signature or deferred direct-submit path
is introduced. Human public write now mounts the existing bearer writer; selected request GET
returns real pending/signed status with only recipient-safe findings/receipt fields.
Matching served models/OpenAPI/CLI/shared TS ship; stored scope/brief bytes remain.
Reviewer staging/native denial are described above; direct-submit stays deferred.
No shared transport/auth/session changes or new owner-read audience follow. Root-owned
two-template public smoke465 and later owner/revision/recovery/UI acceptance remain
dependencies. Retain OPEN/DRAFT/unaccepted/unmerged. Exact grammar:
[Features And Invariants](../../agent-guides/features-and-invariants.md).

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

Compatible cold reopen preserves the complete durable schema, every table's
data and row identities, and the file set, modes and non-database bytes.
Ordinary authority recovery can commit and release an ephemeral lease, changing
SQLite physical pages; closed owners leave no lease or database sidecar residue.
Validator-only and unsupported-reader refusal checks separately retain exact
entire file-byte and mode preservation on a closed database.

An authorized upgrade requires the daemon to be stopped with its configured
home/registration observable and source owners/hosts reconciled. The command
reads bounded existing PID/port/registry evidence and reserves one SQLite
writer; it stops no process and provides no exclusion against an arbitrary
concurrent daemon start. Operator cooperation is a precondition. Active-origin
submissions retain NULL legacy result identity; separate authenticated operation
and INTEGER ordinary-result links preserve the actual result evidence.
U3a(b) internal submission/review/link producers are staged in the stacked draft.
Internal genuine receipts/collection/service joins are the dependency-incomplete
U3b checkpoint above. Public parity, later U3-U6 controls and independent operator
acceptance, including real Founder UI actions, remain pending. This implementation work does not authorize live migration,
enablement, deployment or a Phase1-completion claim.

- **Workflow schema U1A foundation (THR-139).** U1A installs the
  unchanged inert version-1 foundation F plus the canonical coding extension
  from `OrgState.load`, after
  generic `Database` preflight/migrations and before teams, settings,
  membership validation, or orchestrator construction. Deliberate POST /orgs
  creation proves a fresh skeleton, then initializes complete H (F plus the
  reviewed E/G definitions/additions and exact THR139seq445 handoff table) in one
  transaction before attachment; subsequent normal load adds coding.
  Existing startup/reopen/enable never installs E: use `python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug> [--check]`
  only with operator authorization. Check exits0 ready, 3 migration-needed,
  1 refusal (parser2); bounded contention/invalid targets refuse without partial
  installation. Generic
  `Database(...)` construction never installs it: machine-global
  `runtime-audit.db` and every adapter/executor audit caller remain untouched.
  One Database-owned synchronized `BEGIN IMMEDIATE` installs all workflow
  objects, version 1, the singleton `installed_legacy_only` generation 1 owned
  by `workflow_cutover_reconciler`, and its install event; interruption rolls
  back that installation unit; uncertain COMMIT retains unknown outcome.
  Coding storage is added in that same unit, without a document-layout upgrade. Every reopen validates the full canonical
  table/column/default/PK/CHECK/UNIQUE/FK/index/trigger layout and the complete validated
  marker/event history (initial-only references need no org context; progressed
  histories require the actual org slug), refusing missing, extra, malformed, conflicting, newer, or
  wrong-owner state without repair. The legacy authority hook's release
  reference uses a private temporary generic database, applies this same
  literal F/E/G/H/I/J reference plus its independently constructed coding
  companion after full layout/history/data validation, caches independently by
  document layout and coding extension, and hashes every non-null sqlite_master SQL object; it never filters
  workflow objects or installs them into a persistent generic/runtime-audit
  store. `WorkflowCutoverStore` now owns the existing seven-state one-way chain
  from installed_legacy_only through enable_requested, compatibility_verified,
  enabled, disable_requested, draining and drained. Founder-only requests use
  strict action/key/generation input; request/fence commits precede separate
  bounded reconciliation. Every authoritative reread validates the full layout
  and org-bound deterministic event chain; cold load advances only authentic
  committed requests only on ready E/G/H before teams/settings mutation. F keeps
  authentic history and actionable script guidance without recovery advancement.
  GET/preflight never advance.
  SQLite integrity/FKs and contradictory pre-enable work block verification;
  incomplete/nonterminal F5 closures block drain with actual ownership and
  deferred U2D/U4/U5 actions, never cancellation or task/outbox mutation.
  Downgrade is eligible only for pristine F initial history and zero workflow data.
  Every E requires a compatible reader, including empty marker-only E; the marker
  is structural metadata, while draft intents/events are work and their complete
  retained history/result closure is validated. Drain never settles draft work.
  Integrity hashes are cooperative coherence, not hostile same-UID proof.
  S2 now connects initial activation, draft dispatch and callback ownership as
  described below; operator UI and full Phase1 acceptance remain deferred. U1B adds only
  inert, immutable `product-design` template authoring/versioning in
  `runtime/workflows/templates.py`: current active managers publish only in
  their uniquely registered team's org namespace through verified task/session
  provenance, while Founder uses the existing bearer. One synchronized
  `BEGIN IMMEDIATE` revalidates authority and appends canonical JSON bytes,
  SHA-256 digest, gap-free version, CAS pointer and replay receipt in the
  existing U1A tables. Publication never activates, creates tasks/instances,
  dispatches, emits outbox/notification effects or changes cutover/authority
  state. U2A/U2B and the cutover prerequisite are shipped. S2 source adds the
  Founder-only exact-version initial admission and bounded document lifecycle;
  independent review/QA/CI and U3-U6/operator acceptance remain separate.

- **Initial workflow activation and draft ownership (THR139 S2).**
  `WorkflowActivationStore` is attached by `OrgState` and consumed by Founder
  POST/get/list `/workflows/activations`. Initial admission atomically allocates
  the actual task through the unchanged MAX allocator and freezes its root,
  exact template/version, request, authorization, binding/context and intent/event.
  Historical actor/org/key/request replay authenticates retained closure before
  mutable new-admission gates, returns the original receipt200 without queue
  effects, and projects current eligibility separately. New admission returns201
  only after commit; disabled/fenced admission creates no successful empty receipt.
  Profile discovery precedes async serialization, sorted selected profile leases,
  org publication ownership and the synchronized SQLite writer. No host/queue
  effect occurs under durable ownership. Draft/F5/malformed closures are routed
  before legacy startup/run_step/completion/cancellation effects. Startup and
  periodic sweeps rediscover the same authenticated queued intent after lost
  notifications or author-capacity refusal. Periodic discovery checks current
  authority/capacity and deduplicates enqueue only after all leases release;
  claim and prelaunch still arbitrate, without an immediate retry loop. A real session
  is not launch evidence: possible launch is durably reserved, running needs a
  genuine bound handle, and completion needs the exact accepted INTEGER result
  plus finalized host quiescence. Missing acknowledgment/callback/quiescence stays
  workflow-owned uncertain; PID/TTL absence cannot settle it. Cancellation fences
  before containment. The author, including a manager, has only bounded document
  responsibility; no generic manager decision/delegation/fanout is consumed.
  CLI `workflows activate --org ... --from-file /absolute/request.json` and
  `workflows activations list/show` use the existing Founder client; parser2,
  domain/transport1, valid receipt including pending0. This adds no DDL or
  migration or reassignment/reactivation API. S2 alone grants no U3 submission/approval;
  staged U3a(b) ownership is documented separately below.
  Source is not deployment or Phase1/operator acceptance.

## Workflow authority publication (U2A)

`WorkflowAuthorityCoordinator` owns the org-scoped producer half of the
workflow authority contract. Every supported roster, team, repository,
executor/model, reviewer-set, or active-policy writer must durably fence the
org before its authority mutation and publish a coherent digest-addressed
generation afterward. A post-commit publication failure preserves the legacy
writer response but leaves the pointer fenced and machine-readable for cold
recovery; it must never restore a stale ready pointer.

The fence creates the publishing writer's invocation-owned `prepared` journal.
Another coordinator may supersede only that pre-file state; after file-phase
reservation, recovery owns completion. Snapshot reservation, canonical-file
replacement, and the pointer CAS all require the original journal and
`publisher_invocation`, so a stale coordinator cannot adopt a newer fence or
return old authority bytes to ready. Cold recovery re-captures against that
same pre-file journal without replacing it; an incoherent re-capture is
read-only and remains fenced, while concurrent supersession is refused by the
same binding checks. Selector initialization during whole-runtime state loading
(covering daemon startup and runtime register/switch before state publication),
before dynamic org attachment, founder creation
of a new-team manager, pending bootstrap-manager approval, and the legacy
policy GET/release compatibility handlers uses the same coordinated canonical
change. Repeated startup/dynamic checks and existing-team worker creation do
not advance the generation or selector history beyond their own authority
write. Every task/thread/dream/wake/schedule
launch resolver is read-only and refuses an uninitialized selector; it never
initializes authority while launching. An authenticated existing selector
remains a read and does not fence or advance the generation.

Multi-stage async writers take the process-local coordinator gate before
`teams_lock` and retain that gate through their terminal success or
compensation. Request cancellation or SSE disconnect drains started mutating
workers before releasing the gate; admitted request writes also finish their
terminal reconciliation before propagating cancellation, including repeated
cancellation. Startup migration retains the same process gate for its batch.
The shipped durable publication lease is acquired only for each synchronous
canonical mutation and released before filesystem scanning, awaited workspace
bootstrap, cloning, network access, host launch, callbacks, snapshot capture,
or other canonical-input scanning. SQLite stage transactions remain short.
Do not change the existing `org.db_lock -> binding_lease -> synchronized DB
callback` order. Direct same-UID file/DB mutation is outside this cooperative
guarantee.

U2A provides `verify_admission_ready()` for later workflow units but wires no
admission, activation, or dispatch consumer. U2B now ships the cooperative
same-host `ProfileCoordinator`: every supported machine-global profile/adapter
writer acquires the stable owner-only per-profile `flock` before any affected
org publication gate, pre-fences exactly the orgs with active or outstanding
consumers, commits through the existing durable-first writer, and republishes
only a complete coherent closure. The complete acyclic lock order is profile
lease -> org publication lease -> existing writer lock(s) -> the shared
`executor_profiles.yaml` store lock as an innermost leaf. Every save/remove
holds that stable mode-0600 `flock` only around its read/merge/`os.replace`
critical section; the store-lock holder never acquires another lease, writer
lock, or SQLite transaction. Publication paths never acquire a profile or
store lease, and no profile/publication lease or SQLite transaction spans
filesystem scanning, network, host launch, or callbacks. Active Founder creation,
Founder approval, manager revision-CAS executor update, dedicated executor
update, and explicit termination maintain distinct per-agent dependency rows
inside the canonical fence. Pending enrollment/rejection has no active profile
membership. Whole-definition repo/model writers also preserve this relation.
Lifecycle profile leases cover only synchronous canonical mutation and its
compensation, and release before publication capture or awaited bootstrap.
Missing dependency rows are never proof of an empty canonical requirement set.
A profile contender refuses an unfinished ordinary canonical batch before its
global operation claim; startup authority recovery owns interrupted batches. Removal leaves
an outstanding consumer unbound and the org fenced until an explicit coherent
rebind/removal. Cold startup completes interrupted operations once; a
post-commit republish failure preserves the writer's established response while
leaving a machine-readable fenced recovery state. Direct same-UID file/DB edits
remain outside the cooperative guarantee. Dynamic org attachment scans its
canonical profile requirements and authority inputs before taking the corresponding
profile leases. Discovery is bracketed by the existing durable authority revision
and revalidated under the profile-then-org mutation lease; changed captures
retry boundedly or refuse. Synchronization consumes that captured roster without
rescanning, publishes only a validated canonical snapshot and current profile
digest, and joins the shared org map
before releasing them; a non-terminal operation is therefore retried boundedly
or refused and cannot be escaped. Closure coherence compares every org mirror's
profile digest with the current global profile digest before readiness reopens.
Direct-connect route/sweep retries take the same stable profile lease, re-read
the durable projection terminal state before creating a U1A operation/fence,
and only the winner records that existing durable claim and mutates/publishes.
A genuinely empty default org remains attached with no agents and `teams=[]`
when its initial authority publication is fenced by the missing default reviewer.
Attachment proves absence of active and pending definitions and canonical/in-memory
teams outside leases and transactions, brackets that discovery with the durable
revision, and validates it under profile-then-org ownership. It preserves the
initial fenced generation and publication journal; `verify_admission_ready()`
still refuses `authority_pointer_not_ready`. Outstanding dependency or profile
operation evidence, unfinished canonical writers, and stale captures refuse this
exception before synchronization. Reviewer policy and snapshot validation do not
change; subsequent coherent canonical setup uses ordinary publication/recovery.

Adapter approval carries its initially selected intended profile (including no
target) through to the durable registry writer. Under the existing adapter
writer lock, that selection must still match before approval, an idempotent
return, or profile binding. A concurrent supported submission changing the
target refuses with the existing 409 `profile_consumer_changed` conflict before
adapter/profile mutation; ownership releases before a fresh request selects
again. A known target under live contention still returns 409
`profile_coordinator_busy`; ordinary no-target approval succeeds without a
profile. No new profile lease is acquired beneath the adapter writer lock.

U2B still wires no workflow
admission, activation, or dispatch consumer; those remain later units.

### Finite document-review templates and initial drafts (THR-139 seq410)

New `schema_version: 2` / `kind: document-review` definitions compile finite data:
one agent author, 1–3 independent reviewer slots, at most one existing Founder
human, immutable-document-revision output/description, all/current reviewer
membership, approved with optional changes_requested, and either null return or
return-to-author/new/all-prior-receipts. Submission timing is on-completion or
while-active-or-completed capability; it never requires an active author to submit.
Abstract role names are not principals or grants. Actual bindings still require
current same-org active canonical membership, independence and authority/profile
leases. No label, count or outcome supplies code or engineering authority.

The exact @2 compiler/validator/operator-input triple accompanies every immutable
draft/version. Activation request/receipt, authorization, binding and initial-draft
context use @2 families; the context retains both raw definition and compiled
contract. Author allocation and input visibility consume that pinned contract.
Legacy @1 raw bytes, pins, replay digests, task briefs and wire serialization stay
unchanged. Read by immutable definition/pins, never identity.version or current
pointer; full DATA/PIN validation checks all drafts and versions. A pre-generic
reader refuses an org with any @2 data. Such an org needs a compatible reader;
there is no old-reader downgrade promise or automatic history conversion.

F/E DDL, full layout/reference validation and explicit-only existing-org migration
remain unchanged; generic Database/runtime-audit remain workflow-free. Existing
queue, host, callback and recovery owners still produce truthful initial drafts.
Completion is draft-only: submission, review requests/receipts/joins, revision and
mounted UI remain later units. Source implementation is not review/QA/CI acceptance,
operator observation, deployment or Phase1 completion.

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

- **Owner review discovery (THR139 seq506).** Current owner GET `/workflows/activations/{activation_id}/review-selection?proof_kind=owner-human` returns closed `workflow-owner-review-selection@1` navigation IDs, read-only r1 collection and complete advisory return readiness. Relational owner/grants precede decoding and renew at the coherent read boundary; no domain/lease/event/queue/join writes. I and separately admitted selected readers remain required. POST independently retains atomic replay/CAS/current checks. Old daemon discovery is unavailable with dependent actions disabled. Native policy unchanged; mounted UI and behavioral acceptance remain separate. See the four maintained guides and product-design-u0 specification.
