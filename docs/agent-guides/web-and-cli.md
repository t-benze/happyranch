# Web And CLI

## CLI human names and fresh prompt context

The naming CLI provides `identities list/resolve/rename` using the
existing org-local API. Human output shows Name · ID; `--json` preserves raw
fields/IDs. Rename takes `--name` plus positive `--expected-name-revision`, or
`--from-file` containing exactly addressable_name/expected_name_revision. Local
syntax/payload checks precede HTTP; extra binding keys refuse without stripping.
Ambiguous transport/server/malformed-success failures cause one current-ID
readback and an uncertain nonzero exit, never a blind retry. Names confer no
human authentication beyond the existing shared-bearer operator surface.

Human `run --owner`, operator init/approve/reject/model/executor targets and
compose/invite/forward recipients resolve names to canonical IDs. Founder
recipients retain literal `@founder`, never agent participation/ownership.
Task/audit/token/Todo/dream/work-hours human filters resolve names while retaining
historical ID queries and naming-unavailable baseline ID access. Existing action
owners retain eligibility/authorization. Human task/thread/enrollment/Todo/dream/
wake output enriches IDs with explicitly current labels; raw JSON, callback
actors, session proofs, automated targets and historical bodies remain canonical.

Before CLI-managed send/reply or multipart/shared compose uploads, preflight
recognized former body tokens and compose recipients. Forward validates final
quoted+note text. Quoted and email-interior matches follow the unchanged parser;
unknown body text stays intact. Preflight is a read, not a reservation across
HTTP uploads/sends. A rename between requests can leave an independent upload
before the server refuses the message; no rollback/protocol/journal is added.

Task/full/resumed/fallback/dream/wake/schedule prompt source refreshes current
self/founder/ID-selected roster labels, with honest ID-only metadata fallback.
Provider-aware UI editors, name/ID pickers, locales and TypeScript adapters are
published on the open naming PR. The maker handoff attributes actual frontend,
scenario5/8/9 daemon/DB, standalone/served OpenAPI and browser results to their
source. Final independent full review/QA, applicable checks/hooks and guarded
merge remain required. Source publication authorizes no deployment.
See the [current naming contract](../superpowers/specs/2026-10-09-identity-names-core.md).

## Naming v1 core, API and routing source

The bounded API source mounts four existing-org-prefix endpoints: GET
`/identities`, POST `/identities/resolve`, PUT `/agents/{agent_id}/addressable-name`
and PUT `/founder/addressable-name`. Rename body has only strict `addressable_name`
and positive strict integer `expected_name_revision`; extras/missing/null/coercion
refuse. Agent path IDs remain canonical. Read/list/resolve do not reconcile or
write names. Identity views separate canonical_id/kind/lifecycle, addressable_name,
name_revision, canonical_definition_revision and naming_status. Agent roster and
enrollment summaries add addressable_name/name_revision/naming_status while name,
revision and existing ID transport remain canonical; unavailable labels are null.

Resolver body is bounded addresses (1–128 raw names/IDs), context
thread_recipient/task_owner/lookup and optional thread_id only for thread context.
Responses retain input order and typed per-address statuses; unknown lookup is
unknown_identity, former_name exposes current name with eligible=false and never
forwards. ASCII fold and actual owner-bound canonical-ID defaults apply. Pending/
terminated are lookup diagnostics, not eligible agent targets; founder is human,
never task owner/agent participant. Reads grant no action authority/reservation;
later action owners must revalidate. An optional thread must exist locally and
be open, and agent recipients must be participants. Message parser/unknown @text
is unchanged.

Operator editing reuses shared bearer/human dependency, rejects raw body/query
binding-key presence before decoding, and takes no authority from label headers.
CAS/no-op/case-only/former reclaim and audit remain the A service. Lost-response
clients read GET /identities before another CAS. No enrollment-label expansion,
namespace generation or request receipt is added. CLI/prompt consumers are
described above; the provider-aware UI/TS adapters, editors, current labels and
pickers are described in the functional naming UI and API contract below.


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


**Retained artifact foundation A (THR139seq480/477).** Its internal projection
verifies existing document submission and human/agent receipt subjects.
Legacy scope/brief/purpose bytes and historical public models remain unchanged.
B1 adds the matched public model unions described above; native is unchanged. Code-change descriptors are representation-only,
with repository/algorithm/full head/optional base identity and pinned navigation;
no repository reader is mounted. B1 admits the document family; B2 owns
authorized immutable-object reads;
C owns fresh revisions/sign-offs, reassignment/recovery and mounted UI/CLI. Seq493 approves the seq475
reviewer-stage endpoint/native restriction described above. Historical @1/@2/@3 pins,
H revision1/unique intent, selection-before-private-decode and after-wait current
authority retain their owners. Product-design and a differently named one-human
proposal map through the same document adapter; code maps as representation only.
Founder465 public two-template journey/revision acceptance and real Founder UI
Request changes/Sign off remain UNOBSERVED. Ordered coding stages, CI/guarded merge
adapters and fork-join remain Phase2. Source-only dispositions do not prove behavior;
see [Features And Invariants](features-and-invariants.md) for exact descriptor bounds
and execution exclusions. Independent full review/scoped QA renew on the final head.


U3a(c) mounts the H/@3 public handoff and unsigned selected-request surfaces,
with CLI/TypeScript/OpenAPI/native mirrors and F5 callback/replay/cancellation/
portability owner selection. Direct-submit remains deferred; current reviewer-stage/native rules are above. Callbacks alone are unsigned. U3b(d) mounts the existing human bearer
writer and selected pending/signed reads with recipient-safe receipts and findings;
matching closed served models/OpenAPI/CLI/shared TS ship. Stored scope/brief bytes
remain unchanged. Native rules and client.ts are unchanged. Independent same-head assessment, unconditional Source Boundary Lint and
Web Lint (Node 24) under THR291 seq69,
manager-owned two-template public smoke and operator acceptance remain pending.
Source parity is not behavioral PASS or native reachability authority.

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

**G submission-schema migration (THR139 seq395).** The retained F/E-to-G path
precedes explicit G-to-H migration; deliberate fresh org creation now initializes H. Existing F/E startup,
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


## Founder-managed Default (THR296)


C10 covers the existing views in English and Chinese. The shipped header locale
control receives focus after selection; returning to an editor is an explicit
user focus transfer. Open modal drafts use the supported external-tab locale
mirror because the header is outside their focus trap. Locale changes make no
API request or mutation; bounded observations distinguish already-due polling.
Work Hours shows roster loading/error/Retry separately from empty. Engineering's
current v2 editor retains exact text and target through locale/query changes;
Stay/Discard and confirmation remain required. Browser geometry, screenshots
and real after-M acceptance are held independently of mounted Web tests.

The offline roster utility remains operator-only. Its real checked manifest
binds clean candidate/Python3.14 reader and detached runtime registration;
apply/recovery requires that exact digest. Compensation retains the original
successful receipt and records a new coherent original-roster generation in
`compensation-receipt.json`. No transfer UI/API or startup migration is added.
See the migration runbook for commands and AUTHORED-versus-executed case maps.

GET teams/settings retain string manager names for agent teams; human rows
carry manager:null, manager_kind:human, human_manager:founder and is_default.
Both TypeScript wire types and existing real/mock providers accept this shape.
Settings accepts coherent worker additions and preserves pointers/principals.
Removing a still-active declared worker returns teams_consistency_drift and rolls
back. A native registry serialization add/remove roundtrip does not prove valid
HTTP removal; the coordinated offline utility owns the approved team move.
Existing Agent selectors/detail and Work Hours display localized Founder
leadership while preserving executable worker selection. Demoted consultants
are ineligible for manager policy views; no transfer UI is introduced.
The existing roster shows localized loading/error/empty states and Retry;
a recovered first fetch still selects the ordinary worker detail pane.
Worker enrollment distinguishes teams loading, failed fetch with Retry and a
successful empty roster. Retry and locale changes preserve the enrollment
draft; Create requires the selected team to remain in the current roster.
`happyranch run --team default` requires an explicit active executable --owner.
Omitted requests follow task_default_team (legacy Engineering). Founder cannot
be selected as an executor. Normal and recovered review_verdict audits record
agent outcomes, not authenticated human approval.

## Daemon-managed workspace cleanup

`workspace_cleanup.reclamation_actions_enabled` is an internal, strict boolean
configuration key that defaults to `false`; it creates no Web or CLI setting.
When enabled, only the pre-agent cleanup hook may act, and only on a
third-or-later cleanup ordinal whose scheduler-created preclaim owner is
assigned to a registered in-memory `TeamsRegistry` agent and reconciles
to the invocation's initial successful claim (the first two runs stay
report-only), after its current-owner, bounded provenance, one-second deadline,
and at-most-23 read/load admissions, with no refill or recovery. It makes at
most five best-effort consumer calls. A fresh `false` value stops a later
admission but cannot preempt an already admitted call. Each attempt records the
owner `workspace_cleanup_reclamation_attempt` audit before the prompt carries the
known facts, including the literal `after` remainder or `null`. A `None`
consumer result is reported literally as
`refused_or_unavailable`; known partial/failure facts are retained, the
transported remainder is exactly the returned `after` accounting or `null`, and
publication failure stops later calls. The ordinary agent completion summary
remains the normal callback/CLI result surface.

The cleanup contract itself is the ONE shared `workspace-cleanup` TASK system
skill (`requires_repo=false`), reachable from both workspace skill roots. Manual
dispatch requires the exact first line `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN
(manual-dispatch)`; an unmarked manual request is inventory-only. Its bundled
read-only `scripts/check_path_use.py` returns only `clear_observation`,
`blocked`, or `unknown`, using the approved THR-259 seq171/seq185 rule (exact
process name AND bounded cgroup role for fixed daemons; any other unreadable
same-user process is `unknown`).

Cleanup history reads use the backward-compatible `tasks --agent --all-pages
--json` and `audit --all-pages --json` surfaces to exhaust candidate-specific
keyset pages. Scanner execution is job-only and fail-closed on any non-exact,
stale, spoofed, truncated, or incomplete structured receipt. `jobs submit
--json` returns the authenticated submission identity; `jobs show --json` and
`jobs output --json` require the current task/session and return the same closed
receipt containing stored execution identity and complete output accounting.
Human-readable `happyranch jobs show` keeps its existing layout and adds a
`Reason:` line only when the existing job `reason` field is non-null; graceful
daemon-shutdown kills therefore show `failed`, `daemon_shutdown`, and the real
exit code without an API-field change.
An owning origin task branch whose head equals or descends from the candidate,
an owning-task merged PR, and a containing any-task merged PR can preserve a
clean candidate in addition to the accepted durable ref. An existing owning
branch is authoritative: non-containment or failed containment evidence refuses
without merged-PR fallback. The any-task route
uses complete stable double-read discovery, then separately confirms
merged/default-branch state and candidate-to-PR-head containment; discovery
alone and other-task unmerged PRs never count, while owning-branch unmerged
evidence still refuses. Merged integration does not promise original commit
topology. Dirty whole worktrees are never removed, while a literal root
dependency cache can qualify under the separate 24-hour cache gates without
changing tracked source bytes or Git status. The removable worktree must be at
its owning primary checkout's exact registered `.claude/worktrees/<TASK>` path.
The literal root and descendants are walked without following symlinks before
and at action time; nested mounts, cross-device or foreign-owned entries,
protected descendants, incomplete evidence, and identity drift refuse. A
success receipt requires measured root-plus-descendant bytes, actual candidate
absence, and unchanged protected-path identities.

Root-terminal worktree reconciliation has no Web/CLI/configuration surface.
New original owner transitions to `completed`, `failed`, `cancelled` or
`superseded` reconsider only the parent tree, including NULL-parent workflow
tasks. Old child deferrals stay in results/history and become advisory during
this root pass. Fresh preservation, exact scanner and protected identity gates
remain mandatory. The committed start INTEGER id is stable; replay or missing
finish never retries. Async owners await the finite pass off-loop outside locks.
Shared10s work/1s report/1s reap targets are conditional on ordinary I/O, with
explicit caps and partial frontier/remainder. Source checks establish no
behavioral acceptance or deployment. See [the owner/evidence contract](orchestrator-contracts.md#forward-only-terminal-task-worktrees).

## Web UI

### Dashboard projection

`GET /api/v1/orgs/{slug}/dashboard/summary` reads the per-org in-memory
last-known-good projection. `runtime/orchestrator/dashboard_projection.py`
owns coalesced background refresh and atomic `dashboard_projection.json`
publication; the HTTP route never composes the summary under its request lock.
A valid persisted projection serves after restart. Without one, the route
returns 503 until background warm-up succeeds. A refresh failure retains prior
data; `generated_at` describes that data's age and `server_now` is response time.
Database lock wait/hold warnings identify slow operations without changing the
schema. Dashboard/projection tests cover these behaviors.

The SPA supports mutations, including task cancellation/revisit and Settings.
Use `web/src/routes.tsx`, API functions, and the OpenAPI snapshot for the current
surface. The daemon defaults to loopback; remote access uses the connector.

### Internationalization (W1 foundation + W2a shell + W2b onboarding + W2c Settings + W3a Dashboard/Threads + W3b-1 Tasks + W3b-2 Jobs/preview + W4a-1 Health/Dreams + W4b Todos/Work Hours/Audit + W4c Agents/Skills)

The shipping AppBar offers a compact language selector immediately beside the
theme toggle (THR-118 seq88). It shows the current native endonym and offers
English and 简体中文, with each text carrying its own `lang`. Its accessible
name and tooltip translate with the interface. The existing Select primitive
provides keyboard selection, Escape dismissal and focus return. It calls the
same `useI18n` setter as Settings ▸ Preferences ▸ Language, so both controls
agree immediately and remain usable independently of Settings API availability.
Existing saved-choice precedence, browser reload persistence and storage-event
mirroring apply without a second state/persistence owner. Selecting the header
deliberately moves focus to that control; external tab changes preserve focused
editors, drafts and selection. Native desktop restart persistence stays deferred.
Focused ordinary-build evidence uses `w4a-browser-evidence.mjs --slice header-language`
with `--header-case default|tasks|interaction|draft` and the existing W5 startup
cases, without widening the accepted whole-console inventory. In quota-limited
browser venues, `--chrome-temp <short task-owned disk-backed path>` selects
the temporary font-allocation venue for this header slice.

**Ongoing bilingual delivery rule (THR-118 seq81/82):** every new or changed
app-owned heading, action, dialog, tooltip, accessible label, validation,
loading/empty/error state and generated narrative ships en and zh-CN in the
same PR. Use typed catalogs, named parameters, explicit plurals and locale-aware
helpers. Pure shared UI takes localized presentation props. Authored content,
machine identifiers and raw diagnostics stay verbatim.

The focused synthetic browser selections are
`web/scripts/w4a-browser-evidence.mjs --slice agents-safeguard` and
`web/scripts/w3b-jobs-browser-evidence.mjs --slice cascade`, against the ordinary
build with private profiles and ephemeral fixture/CDP ports. They observe
populated/loading/empty/error states in both locales at 390/1440, original task
navigation, control/text bounds and clipping ancestors. The Jobs case measures
every cascade-row descendant's text line boxes on both axes and rejects a row
label (waiting qualifier, task ID) split across lines. The Agents case scrolls
each recent-task label and state message into view and captures it, in
sequential shots when one viewport cannot hold them all, recording the targets
measured visible just before and after each shot. It also requires each
recent-task card header's task ID, status pill, waiting qualifier and age to
read as one line, with every header line box inside the card and viewport on
both axes, at comfortable and compact density. The Agents case uses a
valid prompt revision and checks the retained editor before any new lookup,
authored multiline draft, selection 3:8, focus and request/transport silence in
both switch directions. The unchanged prompt owner tests retain Save/readback
and Cancel contracts; actual browser receipts and human usability review remain
required evidence.

Use this checklist in the existing case record and PR template:

- Exercise both locales and applicable populated/loading/empty/error states at
  390×844 and 1440×900. Check actual affected text/control bounds against clipping
  ancestors, readable whole Chinese text after permitted scrolling, and pointer
  and keyboard reachability; document width alone is insufficient.
- Switch in both directions while retaining the actual mounted nodes, authored
  draft, focus and selection. Observe retained DOM references before fresh lookup
  or refocus, and require zero switch-window HTTP of any method, mutations and
  transport restarts/messages.
- Execute the original actions and navigation with unchanged payloads, raw
  bytes, filters and destinations. Keep existing qualified behavioral tests;
  screenshots and unrelated requests do not prove an action.
- Record source/runtime/command/exit evidence and the test-authoring gate's four
  answers, named tests/producers, attributable RED, byte-exact restoration/GREEN
  and keeper disposition for changed tests. Preserve historical receipts.
- Human review checks meaning and usability. Untranslated owned copy or broken
  Chinese layout requires `REQUEST_CHANGES`; automation establishes only its
  supported structural/completeness boundaries.

Run ordinary Web lint plus the real-config `lint-owned-copy.test.ts` and actual
source `coverage.test.ts` guards. The Node-only source inventory validates the
shipping HTML entry/static aliases and distinguishes declarations/imports from
supported JSX/render-return mounts. Named/namespace router imports and reexports
retain route and parent identity. Additional createRoot renders in reachable
static runtime imports/reexports, including side-effect imports and transitive/
cyclic module graphs, discover direct JSX or statically bound JSX constants.
Unhandled arguments refuse with their source. Type-only edges are excluded;
evaluating an imported module does not mount its unused JSX declarations or
enter uncalled functions/classes. New mounted owners require
source-qualified route/dialog classification; index/wildcard cannot borrow another owner's
status, and full release rejects every english-only namespace. Unsupported
entry/config/computed shapes and fixture promotion refuse with the source to
review. Exact path/declaration/slot/literal/reason exceptions preserve reviewed
raw values and shared defaults; they never exempt neighboring owned copy.
See `web/ARCHITECTURE.md` for the scanner's finite supported shapes and limits.

Current W5b browser contract: English and Simplified Chinese are available. The
entry selects full mode synchronously before the first React text and hands its
one resolution through App/AppShell. AppShell also selects full mode for later
storage changes, deletion, clear and invalid values. Saved en wins Chinese;
saved zh-CN wins English; otherwise ordered supported browser languages apply
(Chinese variants map to zh-CN, with English fallback). There is no startup
preference write or locale-keyed remount. API preview defaults and native
authority/acknowledgement sequencing are unchanged. W1–W4 receipts below retain
their historical builds and preview identities. W5a finite coverage (21 translated/
3 not-applicable inventory) is accepted with PR993; inventory is not exhaustive
rendering proof. Native N0/N1 remain deferred, deployment/live verification separate.

The web console has a first-party, typed English/Simplified-Chinese contract in
`web/src/lib/i18n/` (`locale`, `catalog`, `format`, `coverage`) with the
`I18nProvider` in `web/src/hooks/i18n.tsx`. The initial locale is resolved
synchronously before the first React text and applied to `<html lang>`; the one
resolution is handed through `App`/`AppShell` to the provider so it is never
resolved twice. Production explicitly uses full mode at startup and in AppShell. A valid
`happyranch.ui.locale` choice wins; otherwise ordered supported browser languages
choose the locale, with English fallback. Startup never writes that preference.
Storage failures degrade to an in-memory session with an honest persistence
result, same-origin `storage` events sync other tabs without write loops, and
sequenced acknowledgements prevent a superseded write from reporting durable
success. Adapter `authority` declares ownership: browser authority keeps
`localStorage` and cross-tab events authoritative, while a native adapter's
injected snapshot wins. `web/src/lib/format.ts` remains the canonical display
formatter; `web/src/lib/i18n/format.ts` adds explicit-locale interfaces that
delegate to it, and `formatCount` takes an optional locale (omitting it keeps
the legacy host-default behaviour). An unexpected catalog gap renders the
English message with English plural grammar, never a raw key.

**W5a copy repair:** the mounted Jobs Run/Reject, Settings Capacity/Organization, Tasks Cancel/Revisit/Resolve, Threads Archive/Invite/RemoveParticipant and shared NewThread dialogs pass the existing `common.close` label to their built-in close control. Their actions and focus-return behavior are unchanged. `Markdown` accepts optional `mermaidLoadingLabel`, and `MessageBubble.labels.mermaidLoading` forwards it. KB, Task detail/recall, Threads supply `common.mermaidLoading`; omitted props retain “Rendering diagram…”. A private Markdown-local context carries only that string to the stable code renderer. Suspense remains per Mermaid block, and changing locale preserves loaded diagrams without another render. Authored Markdown/code and raw Mermaid failure source stay verbatim, including text equal to either loading label. Coverage markers remain inventory; W5a rendering acceptance requires the finite mounted-state/browser evidence. W5a finite mounted coverage is accepted (PR993); W5b enables full browser resolution and bilingual availability copy. Desktop N0/N1 remain deferred. Pending-import assertions live in the isolated Markdown.loading.test.tsx file; Markdown.test.tsx SVG/error cases use an independent lazy-module instance so filters and file order do not depend on releasing another test’s import.
**W5a Work Hours reachability:** the agent-detail reconciliation heading and local edit controls wrap within the page, including long raw agent/team names. The reconciliation and overview roster tables keep every column, value and provenance cell in a localized, named, keyboard-focusable horizontal scroll region; focus the region and use the arrow keys to reach the rightmost columns. Shared Button/AppShell and editor/action semantics are unchanged. The earlier 390px document-width checks did not establish child/control or column reachability: historical clipping captures remain failed evidence. The affected ordinary-build browser case is `web/scripts/w4a-browser-evidence.mjs --slice work-hours-reachability`, checking actual control bounds, readable headings and keyboard access to the final column in en/zh-CN at 390/1440, with mounted editor/draft/focus/navigation preservation. W5a finite coverage was independently reviewed, QA-verified and manager-accepted with PR993; W5b enables full browser locale resolution. Historical evidence keeps its original source/build identity. The tier editor now stacks field rows below the small-screen breakpoint, bounds grid children and field/control groups, wraps resets/day controls and full raw title/provenance/selected-timezone text, and allows the existing dialog to scroll vertically. Desktop retains the row hierarchy. Editor child and text-range bounds must be measured against actual dialog content and viewport after scrolling, with pointer hit testing and a real Tab cycle; page/table bounds alone do not establish editor readability. The affected browser cases include all three tiers, windowed/continuous and impact stages, long raw values, both locales at390/1440, and both-direction node/focus/selection/draft preservation with zero locale HTTP. Actual Save/PUT, reset-null and verbatim422 contracts remain owned by TierEditorDialog.test.tsx and work-hours.i18n.test.tsx; screenshots alone do not prove writes.


**W2a** translated the mounted shell — AppBar page titles and controls, Sidebar
navigation/aria/org-switcher/account copy, the root loading and NotFound
fallback, the ErrorBoundary fallback copy, the AddOrgDialog, and the shared
help/palette presentation — and added CJK-capable SYSTEM font fallbacks to
`web/src/design-system/tokens/tokens.css` (no webfont download or dependency).
AddOrgDialog, help and palette also localize the built-in dialog close control:
`DialogContent` gained a backward-compatible optional `closeLabel` prop
(defaulting to the legacy English `Close`) that callers pass from the catalog,
and the pattern/primitive layers remain prop-driven with no locale hook import.
AddOrgDialog stores the product-owned error identity plus the submitted slug and
re-translates at render time, so an already-visible mapped error follows a
locale switch without resubmission while unknown external daemon detail stays
verbatim.

**W2b** translated the onboarding route (`/onboarding`): `OnboardingPage`
(first-run vs returning welcome, create/creating/success, the read-only
broken-org list and the executor-prereq panel), the `ConnectRuntimeStep`
wrapper chrome, and the shared `src/shared/connect/ConnectFlow.tsx` bodies
(built-in/custom modes, waiting/committing/connected, retryable + terminal
failure, clear/recovery, copy feedback and aria text). The add-org error
classifier/renderer is now the single shared
`src/lib/addOrgError.ts` consumed by both AddOrgDialog and the
onboarding create step. Because `ConnectFlow` is shared with Settings ▸
Executors, translating it also localizes that connect body, but the `settings`
namespace stays `english-only` in the manifest (the surrounding Settings chrome
and sections are not translated) and Settings is not claimed as translated.
At W2b, user-entered slugs, registered tool names/paths, the slug regex, broken-org raw
errors and the generated copy-paste CLI prompt (raw step ids, tokens, routes)
stayed byte-for-byte verbatim; mapped categories re-translated on a locale switch
with no resubmission. THR298 seq34 supersedes the whole-custom-prompt byte
guarantee: app-owned custom capability/update guidance translates, while
machine/executable fields, raw identifiers, routes, tokens, commands and authored
values stay verbatim. The whole custom prompt therefore changes across locales;
historical W2b outcomes and built-in prompt claims are preserved.

**W2c** translated the Settings surface (`/orgs/:slug/settings/*`): the page
header, sub-nav, API loading/error copy and panel headings, plus the
Organization, Executors (registered list, custom profiles, binary paths) and
Capacity section bodies. Raw daemon errors, identifiers, executor and
agent names, config keys, paths, commands and every capacity number stay
verbatim; only product-owned surrounding copy is localized. The shared
`EligibilityEditorDialog` (mounted by Organization but owned by Work Hours)
stayed English until W4; W4b translates it. W2c also builds the client-only **Settings ▸
Preferences** language selector (`sections/PreferencesSection.tsx`: English /
简体中文 endonym radios with their own `lang`, immediate apply through the W1
`setLocale`, `<html lang>` update, honest pending/saved/failed status) and
splits the Settings shell so the `preferences` route renders OUTSIDE the
`useSettings` loading/error/data gate; every other panel keeps that gate
unchanged. Choosing a language never writes org settings or issues any API
request. Until W3b-2 the selector was closed in production by a
`VITE_ENABLE_I18N_PREFERENCES` build gate (`languagePreferenceGate.ts`, now
removed — see W3b-2 below). The W2c browser harness
(`web/scripts/w2c-preferences-browser-evidence.mjs`) originally built a separate
preview dist with that flag; since W3b-2 both of its dists are ordinary builds
and its historical case A asserted the mounted selector (route renders, unset
preference stays English). The W5b ordinary cases own current full-mode defaults. It also proves that state-held Settings messages follow a locale
switch (the Organization Work Hours banner re-translates in place) while raw
daemon diagnostics stay verbatim, with causal negatives for a pre-translated
banner (`--defect-dist`) and a translated diagnostic. At that historical W2c/W3b-2 stage there was no browser/system
language defaulting; W5b supersedes the unset/invalid-English default.

**W3a** translated the mounted Dashboard and Threads route families (`features/dashboard/**`, `features/threads/**` list/detail/composer/strips/dialogs and the shared `shared/threads/NewThreadDialog.tsx` it mounts); pure design-system patterns (Composer, ThreadHeader, InboxRow, StatValue, CrescentMoonBadge, RecipientsInput, MentionTextarea, …) take optional localized label props with English defaults, so their other callers are unchanged. Thread errors are held as locale-neutral `ThreadErrorView` descriptors (`lib/threadErrors.ts`: mapped catalog key/params, or `raw` text rendered byte-for-byte even when empty or equal to a catalog string) and rendered at render time. Authored thread titles, message Markdown, names, IDs, filenames/hrefs, raw delivery payloads and machine values stay verbatim; a locale switch keeps drafts, attachments, selection, open dialogs and focus and issues no request. W3a browser evidence runs `scripts/w3a-core-browser-evidence.mjs` against the ORDINARY dist only (storage-event switching, no in-app instrumentation); since W3b-2 its gate case G asserts the ordinary bundle contains the Preferences selector, `/settings/preferences` renders it, and historically an unset Chinese navigator stayed English. W5b ordinary activation cases establish Chinese fallback; the former preview-dist positive control is gone with the flag.

**W3b-1** translated the mounted Tasks route family (`tasks`, `tasks/:task_id`: `features/tasks/**` list/detail panes, filters, group/status/rollup presentation, fan-out band, chain/lineage timeline, recall tree, activity log, property rail and execution-status card, loading/empty/error states, and the owned Cancel/Revisit/ResolveEscalation dialogs). The shared `StatusBadge` takes an optional localized `waitingLabels` prop with an English default, so its Jobs and TaskCard callers are unchanged. Dialog errors are held as locale-neutral `TaskErrorView` descriptors (`features/tasks/strings.ts`: mapped catalog key; an unmapped daemon code or, with no code, a non-empty string diagnostic rendered verbatim; otherwise the localized fallback) and the Revisit validation message is a state-held key, so both re-translate in place. Briefs, summaries, notes, agent names, task/thread/job IDs, status/block-kind/verdict/reason machine values, unknown escalation flavors and work-status states (the daemon label), raw event actions/payloads, hrefs and shortcut keys stay verbatim; group and lineage React keys stay locale-neutral, so a locale switch keeps rows, selection, open dialogs, typed drafts and focus and issues no request. W3b-1 browser evidence runs `scripts/w3b-tasks-browser-evidence.mjs` against the ORDINARY dist.

**W3b-2** translated the mounted Jobs route family (`jobs`, `jobs/:job_id`: `features/jobs/**` list chrome, needs-you callout, status groups, columns and pending relative age; detail states, header actions, command card, If-approved cascade, gated notice, property rail and output panel; the owned Run/Reject dialogs). Daemon values stay verbatim: job/task IDs, titles, script text, rationale, agent names, interpreter/cwd values, job status tokens and `exit <code>`, exit codes, stdout/stderr and their stream names, the live `[done]` log line and rejection/failure reasons. Run/Reject/Stop errors are locale-neutral `JobErrorView` descriptors (`features/jobs/strings.ts` `classifyJobError`, the same F1 boundary as `classifyTaskError`: recognized code -> catalog key; unknown non-empty code -> raw; no/empty code with a non-empty string diagnostic -> verbatim; otherwise the localized fallback), replacing the former English `Error <status>: API <status> (<code>)` text. W3b-2 also **enables the opt-in language preview**: the `languagePreferenceGate.ts` / `VITE_ENABLE_I18N_PREFERENCES` gate is removed, so Settings ▸ Preferences ▸ Language is mounted in ordinary production builds. An unset preference stays English (production resolves in `preview` mode and never reads the browser language), and the selector discloses that secondary, not-yet-translated pages may still appear in English (`settings.preferences.coverageDisclosure`). Browser evidence runs `scripts/w3b-jobs-browser-evidence.mjs` against the ORDINARY dist.

**W4a-1** translated the mounted Runtime Health and Dreams routes (`features/health/HealthPage.tsx`: header, history-window toggle, stat cards, uptime/relative-age units, loop and HTTP-latency tables, trends; `features/dreams/**`: header eyebrow plural, feed, status pills, counts, quiet state, overview rail and the dream detail drawer with its candidate review gate). Daemon values (loop names, route templates, dream IDs, agent names, local dates, summaries, transcripts, error text, candidate title/slug/topic/rationale/body, KB slugs, unknown status tokens) stay verbatim. Accept/Dismiss errors are `DreamErrorView` descriptors (`features/dreams/strings.ts` `classifyDreamError`, the same F1 boundary as `classifyJobError`); `DREAM_STRINGS` is replaced by `dreams.*` catalog keys. Browser evidence: `scripts/w4a-browser-evidence.mjs` against the ORDINARY dist; its `API_ROUTES`/`VIEW_ROUTES`/`SWITCH_ROUTES` tables are extended by later W4 slices (W4c Agents/Skills and W4d-1 KB/Artifacts shipped; Usage presentation W4d-2).

**W4b** translated the mounted Todos (`features/todos/**`: list, detail, status pills, rows, recurrence/timezone presentation, Confirm/Edit dialogs), Work Hours (`features/work-hours-config/**`: overview, wakes, agent detail, TierEditorDialog; plus the Work Hours-owned `shared/work-hours/EligibilityEditorDialog.tsx` that Settings ▸ Organization mounts and its `ErrorPanel`) and Audit (`features/audit/**`: page, timeline, filters and the narrative, whose sentences are catalog templates with interpolation) route families. Agent names, task/schedule IDs, actions, timezones, cron/recurrence values and raw payload/error values stay verbatim; every visible date/time goes through `lib/i18n/format.ts` (`formatDateShapeFor` named shapes; feature-local `Intl.DateTimeFormat('en-US' | 'en-CA')` remains only for timezone-conversion parsing and `<input>` values, enforced by a `format.test.ts` source scan). Count-bearing Todos/Audit copy uses per-locale plural objects selected by a numeric `count`, and the two Work Hours dialogs pass `closeLabel={t('common.close')}`. Error sites use the same F1 boundary through feature-local `strings.ts` classifiers. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

**W4c** translated the mounted Agents (`features/agents/**`: roster list, agent detail pane/drawer, pending enrollments, AddAgentDialog, TeamEscalationPolicyPage/Card) and Skills (`features/skills/**`: catalog, validation, skill detail + assignment panel, custom-skill list/create/detail) route families. User/daemon values (agent names, roles and team identifiers without translation or title-casing, team-policy bodies, contract ids, digests, skill names/slugs/descriptions/SKILL.md bodies, versions, provenance values) stay verbatim; visible dates/times, including policy release/activation history and custom-skill purge completion, use named shapes in `lib/i18n/format.ts` (`dateTime` for those timestamps; en/zh-CN rendered-state regressions complement the source scan, which rejects direct locale-formatting calls), count-bearing copy uses plural objects, in-scope dialogs pass `closeLabel={t('common.close')}`, and error sites use the F1 boundary through `classifyAgentError` / `classifySkillError`. Below `md` the Agents roster stacks above the detail pane (height-capped, internally scrolling) instead of a fixed 244px rail, so the detail is not squeezed at 390px. The detail main pane uses `max-md:min-h-0` only below `md`; at `md` and up its base computed min-height remains `auto`. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

**W4d-1** translates KB and Artifacts chrome, including list/detail/candidates,
search/filter/breadcrumb/empty/loading/error/Retry, gated KB Compose labels and
its supported DialogContent close control, and artifact upload/download/delete
confirmation. `VITE_ENABLE_KB_COMPOSE` remains unchanged. Candidate/Compose and
artifact action errors are pure F1 category/raw descriptors rendered through the
current translator, so generated messages retranslate without repeating a
mutation; unknown codes and nonblank raw diagnostics remain verbatim, while
absent/blank diagnostics use the localized fallback. KB authored content,
type/topic/tags/slug/names/IDs and artifact canonical names, path segments,
file-derived titles/provenance identifiers remain byte-verbatim. Only artifact
client-derived type LABELS translate; classification/parsing and stored data
remain unchanged. Counts use explicit-locale display plus plural objects.
KB card/detail share relative-age rounding and catalog units, with explicit
unavailable display for invalid dates. Artifact filename calendar dates use the
existing `monthDayYear` shape in UTC without shifting the day; invalid calendar
dates render an unavailable label. Modified times use viewer-local `dateTime`
with explicit locale. The English modified-time shape now zero-pads the hour
(e.g. `02:05 PM`), and invalid KB age displays `Age unavailable` instead of
`NaNd`; F1 replaces synthetic HTTP action failures with raw diagnostics or
localized fallbacks. Artifact bytes use `formatAttachmentSizeFor` with the same
binary thresholds/rounding; other attachment consumers retain their legacy
optional-locale behavior. DrawerContent has no closeLabel/control; Compose's
DialogContent does, and is localized. The KB drawer fits below 640px without
changing desktop width. At that narrow breakpoint the scrollable filter rail
stacks above the feed, and the header actions stack below the title so list
copy stays readable; desktop retains the side rail and header layout. Raw KB
type badges retain their stored case (for example, `sop` rather than `SOP`)
in both locales instead of applying a CSS uppercase transform. `kb` and
`artifacts` coverage is translated; `usage` is translated by W4d-2. W5b supersedes the historical preview default with full browser resolution
and bilingual availability disclosure; native N0/N1 remain deferred.
Browser evidence adds representative KB list/detail/candidates and artifact
list/folder/upload rows to `web/scripts/w4a-browser-evidence.mjs`, plus one
upload filename/selected File/focus/control-preservation switch in both directions
with zero requests. Final-head evidence is recorded in the task handoff.

System Assistant mounted copy is retired under THR-294.
W5a finite mounted coverage is accepted with PR993. W5b replaces the historical
preview default/disclosure with bilingual availability and full browser-language
resolution. Native preference persistence remains deferred to N0/N1.
`web/src/lib/i18n/coverage.ts` marks exactly the W2a/W2b/W2c/W3a/W3b-1/W3b-2/W4a-1/W4b/W4c/W4d-1/W4d-2-migrated namespaces
(`root-shell`, `not-found`, `app-shell`, `help-and-palette`, `onboarding`,
`settings`, `dashboard`, `threads`, `tasks`, `jobs`, `health`, `dreams`, `todos`, `work-hours`, `audit`, `agents`, `skills`, `kb`, `artifacts`, `usage`) `translated` and every other mounted route namespace `english-only` (copy-free
redirects `not-applicable`), listing the actual mounted dialogs, so English
fallback is never mistaken for coverage. Foundation browser evidence (isolated
Storybook probe + the real `main.tsx` startup in headless Chrome) runs via
`web/scripts/i18n-browser-evidence.mjs`; W2a shell evidence runs via
`web/scripts/w2a-shell-browser-evidence.mjs` under an independent
`I18N_W2A_EVIDENCE`-gated, test-only build injection. It reads the actual first
committed Sidebar/AppBar DOM plus `<html lang>` and the real navigator
read-back, retains a causal negative control (`I18N_W2A_EVIDENCE=negative`:
wrong first shell, later corrected, must be rejected by the same predicate), and
covers both locales across the observed 1440x900/390x844 light/dark
combinations for root loading/no-org/NotFound/help (S11/S16)/AddOrg
(S12/S17)/error/dormant-palette. In both switch directions it records the actual
`document.activeElement`, retained DOM node identity, open state and
selection/value for the help non-default tab (S16), the AddOrg typed slug +
mapped error (S12 wide-light; S17 zh-narrow-light/en-narrow-dark/zh-wide-dark)
and the palette query + non-default selected row (S13 wide-light; S18
zh-narrow-light/en-narrow-dark/zh-wide-dark), and asserts each switch window
issues no `PUT /settings/org` and no `POST /api/v1/orgs` (the palette windows
also assert zero `/api/` requests, cache-only, per direction). It exercises the
palette's localized X close control (S19) with native Enter/Space/Escape in both
locales with populated and empty results (closes once, zero selection, unchanged
pathname) while the search-input and non-default-row Enter still select once, and
adds CJK-font and viewport-containment checks. Positive receipt: 375/375
assertions, 30 PNGs. W2b onboarding evidence runs via
`web/scripts/w2b-onboarding-browser-evidence.mjs` against the ORDINARY
`web/dist` bundle and a synthetic `/api/v1` stub, driving the real `/onboarding`
route with NO evidence-only bundle instrumentation: locale switches go through
the supported browser `localStorage` preference plus a same-origin `storage`
event (there is no public selector), so the harness proves the shipping bundle
already satisfies the acceptance predicate. It covers first-run/returning
routing, built-in/custom connect modes, the waiting/committed prompt bytes
preserved across en→zh-CN→en, mapped-vs-raw error handling, success, the
broken-org list and the prereq panel. For S4-S6 and S8-S10, each en→zh-CN and
zh-CN→en transition records the actual `document.activeElement`, retained
test-only DOM identity where stable, open phase/mode and state-specific raw
values before and after. Every immediately scoped transition window asserts no
`PUT /settings/org`, duplicate `POST /api/v1/orgs`, connect/mint mutation, or
other `/api/` request; S4-S6 retain exactly one original create and S8 retains
exactly one original mint. The exact assertion and PNG counts are bound to the
pushed `receipt.json`.
These are historical W2b receipts. THR298 seq34 supersedes their
whole-custom-prompt byte predicate for app-owned custom capability/update
guidance, which now translates; machine/executable fields, raw identifiers,
routes, tokens, commands and authored values remain verbatim. Historical
outcomes and built-in prompt claims are unchanged.
Current contract:
`docs/superpowers/specs/2026-09-20-web-i18n-design.md`.

### Web contract and navigation

Layer rules, boundary rules, and agent-callback omissions live in `web/ARCHITECTURE.md`. Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

Every browser-callable daemon route maps to one TypeScript function in `web/src/lib/api/`. The maintained snapshot and coverage consumer are:

- Snapshot: `tests/contract/openapi.json`; its Python unit test is retired. Regenerate intentional changes with `uv run python scripts/generate_openapi_snapshot.py --write`.
- TypeScript: `web/src/test/openapi-coverage.test.ts` asserts every documented path is either included with a TS mirror or excluded with justification.

### Tasks list

The Tasks heading, grouping controls, inline filters and root rows share one
scroll owner. Status, Agent and Thread grouping use loaded matching roots;
counts do not claim a server total or count subtasks. Status grouping preserves
all seven lifecycle groups in display order: Waiting on you, In progress,
Pending, Failed, Completed, Cancelled, Resolved. Completed excludes superseded,
which appears in Resolved; display order does not change severity rollups or
filter-option order. Resting list rows and their lineage use the existing raised
section surface (white in light mode, semantic raised override in dark mode),
with distinct hover styling. Only superseded rows are dimmed in every grouping.
Tasks opts into blue in-progress and amber escalated badges and its own AppBar metrics; other
surfaces retain their defaults.

Filter supports status and exact assigned-agent name. Apply submits both drafts;
closing the panel leaves applied values unchanged. Clear omits both parameters.
An empty agent means no agent filter, not an unassigned-only request. Applied
values remain visible, including for successful empty results. A new org/filter
request starts without another context's cursor; returning to a cached context
retains its own pages. Local recovery ownership resets on context changes.
`Waiting on you` uses a separate root traversal with exact
`status=escalated`; it is never derived from loaded ordinary pages or a
severity rollup. Its rows are root-only and own the presentation when an
ordinary page contains the same task id. While its cursor remains, wording is
non-exact (`50+ waiting on you`); only an exhausted traversal may show an
exact count. The ordinary cursor order is unchanged.
When the attention traversal has rows, they render as the FIRST group inside the
shared list shell — the same `tasks-group` wrapper, group heading (attention
dot + `Waiting on you`) and raised rows-card as every status group — directly
after the task column header and above the status groups; there is no separate
outer padded/inset card. When there are no escalated roots (and attention is
not loading or errored), no `Waiting on you` group, heading or empty card is
rendered. If attention has no rows while its traversal loads or errors, the
ordinary list remains usable and the attention loading/error state renders
separately; a non-empty escalated group can coexist with an ordinary initial
loading/error state without hiding the shared list shell, and a successful
empty ordinary result must not claim `No tasks` while escalated rows are
visible.
Ordinary status/agent filters remain their established independent traversal;
they do not alter the separate attention query. Initial attention failures make
no count claim and show Retry. A failed attention refresh or continuation keeps
the deduplicated loaded rows and their honest current count visible with a
stale/error warning; continuation Retry resumes the failed attention cursor,
while ordinary pagination retains its own separate page Retry. Waiting rows
share the ordinary responsive row treatment because they live under the shared
list shell; its page-local load-more and Retry controls wrap within the group at
narrow widths; shared button behavior is unchanged.
Initial ordinary failures show an
explicit Retry; failed ordinary refreshes retain cached rows and a stale warning;
failed ordinary pagination retains loaded rows and requires its separate page Retry.
Manual refresh retries the active context's loaded pages. Subtask severity
rollups are count-free and reflect the worst **current** status of the root's
`parent_task_id` subtree; a historical FAILED descendant whose same-parent
revisit lineage leaves no unresolved FAILED leaf does not dominate, while the
root's own severity and all escalations are preserved (see
`features-and-invariants.md` §Bounded failure-recovery). Task detail owns
subtask browsing. No New task flow is
exposed by this list.

### Current escalation reason display

Task detail (including its root recall-tree Outcome), `happyranch details`,
and the dashboard Waiting on you inbox use
the additive `escalation_reason` read projection. Ordinary escalations retain
their existing `task.note`, API values, and presentation. When the current
escalation audit is an `authority_v2_refusal`, `primary` comes only from an
`orchestration_step` whose decision is `escalate` within the current episode;
the previous and current `escalation` audit IDs are the exclusive episode
boundaries. The UI and CLI show that manager-authored text first, followed by
the server-owned plain-English explanation for the refusal code. A refusal
episode with no escalate decision shows only the explanation, and an unknown
code is shown verbatim. A resolved older episode is never searched. On task
detail the two labels (`tasks.detail.escalationReason`,
`tasks.detail.automaticEscalation`) follow the UI locale (W3b-1); the
`primary`/`secondary` values are daemon text rendered verbatim in every locale.

The read is exact-task scoped and bounded. It does not alter `tasks.note`, the
refusal finalizer, any audit payload, or the `audit_log.task_id` scope-prefix
contract. Task list rows and the resolution dialog do not display escalation
reason text; their behavior is unchanged.

### Thread-detail system rows

The live `ThreadDetailTranscript` renders system events through its local
`SystemDivider`: one separator above a monospaced description, inline icon
and task links, then trailing system-event metadata. Short and long rows use
the transcript content width inside the existing padding. Text and unbroken
identifiers wrap; the timestamp remains available in the row tooltip.
`describeSystem` displays the complete supplied task-completion summary and
escalation reason without a character limit, preserving line breaks and safely
rendering HTML-like content as literal text. It does not expose additional
payload fields. Ordinary message bubbles and terminal responder strips retain
their separate rendering paths. CLI transcript formatting is unchanged.

### Sidebar height and scrolling

The AppShell keeps the sidebar within the window height. Its organization
switcher and Settings/account footer do not shrink; the primary navigation
uses the remaining space and scrolls internally when needed. Navigation items
and typography retain their existing sizes, order and destinations. The rail
remains 244px wide on desktop and collapses to 56px below the `md` breakpoint.

The navigation container uses `min-h-0` to permit flex shrinking and `relative`
to contain the collapsed rail's absolutely positioned accessible labels.
Internal padding and scroll padding reserve room for keyboard focus rings.
Routed page content scrolls separately in its own container (for example,
`ContentWrap`), inside the shell's `overflow-hidden` main area. An overflow
container does not contain absolutely positioned descendants, so visually
hidden (`sr-only`) text inside page content needs a positioned ancestor within
that scroller (Usage sections are `relative`); otherwise it extends the document
and the window scrolls the whole shell, leaving a blank band below it.

`Sidebar.test.tsx` covers navigation/footer behavior and the overflow structure;
jsdom does not verify layout. For browser verification, use the supported
workflow in `web/scripts/screenshot-harness/README.md` with the real routed app
and valid local fixtures. Compare a short-window baseline with the correction;
check internal navigation scrolling, reachable footer controls, keyboard focus,
org switching, page scrolling, resizing, mobile collapse and both themes.
Record the source/build, viewport, screenshots, geometry and observed errors
in task evidence. Keep any font substitutions explicit when reporting visual
fidelity.

### Settings

The Settings surface ships as a full page (`web/src/features/settings/SettingsPage.tsx`) at the `/orgs/:slug/settings/*` route, entered from the footer-pinned **Settings** item in the Sidebar, with four sub-nav links, in this order: Capacity · Organization · Executors · Preferences. Below640px these links wrap above the panel; at640px and wider the existing left rail remains. Preferences keeps full readable copy and a locally bounded panel. The Settings root, retired `assistant`, `system` and `agents` subroutes, and unknown subroutes resolve to Capacity with replace navigation outside the settings data gate. (`SettingsDialog` is retained unmounted for direct tests; it is not an application or prototype entry point.) It shows:

- **Capacity** — stages the paired daemon-wide `queue_workers` and
  `host_global_session_cap` values for a future operator-controlled restart.
  Saving never applies live and the page cannot restart the daemon: the running
  readouts are observations and are unchanged by a save. The panel holds `base`
  (accepted only from a usable read or a usable success), the operator's
  `draft`, `latest` observations and an immutable `submission` record
  separately; a later read never silently rebases a dirty or unresolved draft,
  and only an explicit rebase / accept-latest moves `base`. Concurrency is the
  daemon's quoted strong `If-Match` revision. Numeric input is validated as
  canonical positive-decimal TEXT before any `Number` conversion, so an
  operator value outside the exactly-representable range is refused with the
  entered text preserved rather than silently rounded; that bound is an EDITOR
  representation limit and is not an API maximum. A server numeric that did not
  survive `JSON.parse` as a safe integer is withheld rather than displayed
  rounded — see the capacity note in `runtime-and-configuration.md` for the
  residual limitation this does NOT close. "Usable" means the same thing at
  every entry point: one capacity-local classifier decides it, and the provider
  applies it before a read observation is called usable or a write result
  enters the capacity cache, so a response the editor rejects can never become
  accepted cached data.
  Three refusals are load-bearing and are enforced at the request handler, not
  only on the control. A refresh that FAILED leaves the last usable values on
  screen under a `Last known` label with the receipt of the response that
  actually produced them — the retained values carry their own receipt even when
  a later request succeeds with an unusable body (that response still advances
  the separate provider receipt, but never relabels the retained values), and a
  byte-identical successful response still dates those retained values with its
  OWN receipt even though React Query structurally shares the value object —
  keeps the draft, reason and acknowledgment, and
  blocks the write until a genuinely successful, usable read recovers — a
  refetch result that merely carries cached data beside an error is not a
  success. An unresolved publication outcome (typed `config_publication_uncertain`,
  or a lost response / unclassified failure) is held as its own state: repeated
  refused Save clicks and an ordinary Discard change the banner and reset the
  editor but never resolve it, because neither establishes what the daemon
  persisted. And `latest` observations are ordered by the order this editor
  accepted them, so a newer verified read supersedes an older 409 body for both
  rebase and accept-latest; an accepted write then clears the reconciliation
  state its own response made obsolete and finishes in a coherent clean state.
  The SUBMITTING editor recognises its own write by the exact request: the
  capacity mutation slot (`CapacityMutationLike.settlementOf`; the shared
  `MutationLike` is not widened) reports which provider settlement that request
  object produced, recorded before the cache write, so the editor skips exactly
  that one observation whenever its render is flushed — never a window in
  which the request happened to be in flight and never the revision it
  accepted. A revision is a content hash, so another editor saving or restoring
  the same bytes is a different settlement and is observed normally. A
  receipt of the editor's own base revision is ordinarily a no-op (no notice,
  no target), but equality to the base is not proof that nothing happened
  since: when a pending reconciliation target names another revision, a later
  observation restoring the base bytes supersedes that target like any later
  observation, so rebase and accept-latest act on the restored revision
  rather than on an obsolete one, and the explicit choice is still required. Every
  observation that is not this editor's own settlement — including another
  editor's write that lands while this editor's request is still pending — is
  handled at once by the ordinary rules: adopted when clean, recorded for an
  explicit choice when dirty or unresolved. Nothing is held back awaiting the
  outcome, so a rejected, unknown or unusable own result leaves that external
  write available and still requires an explicit rebase / accept-latest before
  any further PUT. An accepted own write fences only observations that settled
  BEFORE its own settlement; one that settled after it is adopted by the
  now-clean editor instead of being cleared. Any other editor mounted on the
  SAME client therefore treats a write it did not settle as the external change
  it is, rather than staying stale on an older base and revision; its next
  deliberate save carries the adopted (or explicitly chosen) revision.
  Draft consequence arithmetic uses the RESOLVED per-key next-start values, so
  an environment-shadowed key contributes the environment's value rather than
  the draft the environment will shadow.
  Leaving the panel with an unsaved draft — or with an unresolved publication
  outcome — is intercepted by a confirmation dialog. **Stay on page** keeps the
  route, keeps the values, reason and acknowledgment byte-identical, and
  returns focus to the control that had it before the dialog opened;
  **Discard and continue** completes the navigation. The panel carries its own
  focus-ring and primary-action tone treatment, selected from existing tokens at
  the call site rather than by changing a shared primitive or token definition,
  because the shared ring token is translucent enough to fall below the accepted
  3:1 ring threshold on this screen's surfaces. That treatment covers the
  confirmation dialog too, including the dialog Close control the shared
  primitive renders: the dialog is PORTALLED, so it inherits nothing from the
  panel wrapper and is styled and measured explicitly.
  `scripts/screenshot-harness/capacity-states.mjs` gates both, at both desktop
  widths in both themes. It declares the finite set of keyboard OPERATIONS it
  requires — the details disclosure; the acknowledgment that enables Save;
  Check saved values after a real uncertain write, with its deliberate rebase
  and the separate manual save that follows; conflict rebase; conflict
  accept-latest and the separate manual save that follows it; the leave
  dialog's Stay and its confirmed departure — independently of what any run
  records, so a required operation that is missing fails the run exactly like
  one that failed. An unfiltered run is judged against that complete inventory
  at every viewport and theme whatever scenario list it executed, so deleting a
  scenario — its definition, its callback or its records — fails the run rather
  than deleting its expectation; only an explicitly filtered run narrows, and it
  is labelled partial and never reported as acceptance. Where
  an operation has a pure verdict (Check semantics, the C1 receipt sequence)
  the gate recomputes it from the recorded evidence rather than trusting a
  recorded pass, and a focused failed-reread control proves that cached values
  beside an error are not a successful Check. While the real portalled dialog
  is open — in its own isolated context, so a measurement can never confirm a
  departure — every dialog string is contrast-sampled and the enabled
  `Discard and continue` is measured through real rest / hover / active pointer
  phases; a missing sample, a missing phase or a failed one fails the run. It
  measures every focus ring after cumulative opacity, and self-tests its own
  computed-visibility predicate against positive and negative clipping
  controls. `--gate-selftest <MANIFEST.json>` runs that same acceptance gate
  over a recorded manifest and over finite corruptions of it — missing
  operations, a removed scenario callback, a deleted scenario passed to the
  gate exactly as the run would then pass it, a full-run caller narrowing
  viewports or themes, a failed operation, a weakened Check
  assertion, a missing walk, missing dialog samples, a C1 receipt that went
  backwards — and fails unless every one of them is fatal and the unchanged
  manifest passes. Because the shell scrolls internally, a state whose evidence
  is a top-of-panel banner — the retained-receipt / unverified-read /
  unrepresentable-value notices — declares that banner as its capture subject,
  and a subject that is not inside the captured frame fails the run. Two states
  drive the receipt rule end to end under a pinned browser clock: a usable
  read, a BYTE-IDENTICAL successful 200 (proved identical by the served body
  hash, not by unchanged values), then a failed read whose retained values must
  still carry the identical response's OWN receipt, and finally a usable
  recovery that advances it.
- **Org** (editable, Phase 2) — org-level settings: session timeout override, dreaming schedule (enabled, schedule time/timezone, catch-up-on-startup, agent mode, include/exclude agent names), browser-managed threads config (enabled and invocation timeout), and **working_hours** (THR-035: the Work-Hours Config UI — feature on/off switch, org-level eligibility selector, and the raw per-tier schedule blocks `default` / `teams` / `overrides`).
- **Executors** — effective machine executor registry, custom CLI lifecycle, and recovery.

The response includes operator-only `reviewer_agents` and `threads.default_turn_cap`; neither has a browser control. Removing the former System rail does not move queue-worker facts into Health. The retired maximum orchestration step setting is absent from the settings API and browser surfaces; the historical step counter remains monotonic telemetry, not an execution limit.

**Backend routes:**

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/orgs/{slug}/settings` | Read-only System + Org snapshot (includes the raw per-tier `working_hours` blocks for the reconciliation view) |
| `PUT` | `/api/v1/orgs/{slug}/settings/org` | Partial-update editable Org settings |
| `PUT` | `/api/v1/orgs/{slug}/settings/teams` | Worker-membership editing for teams |
| `GET` | `/api/v1/orgs/{slug}/work-hours/next-wakes` | Preview the next N wake timestamps for an agent's resolved effective schedule |

The serializer is an allow-list: no secret fields (permission_mode, codex_sandbox_mode, feishu credentials, daemon bind/port, allow_rules) are ever serialized. `extra='forbid'` on the PUT body rejects unknown/sensitive keys with 422. `save_org_config` deep-merges only allow-listed keys (`dreaming`, `threads`, `session_timeout_seconds`, `working_hours`) and carries through all unmanaged blocks verbatim. Tests recursively assert key-safety invariants (`tests/daemon/test_routes_settings.py`).

**Work-Hours Config (THR-035):** `working_hours` writes reuse the existing validate-then-atomic-write path in `save_org_config` — the candidate config is validated by `_build_org_config` / `_parse_working_hours` (the same path config-load uses), so an invalid config can never reach disk; the last-known-good keeps running. `enabled` is a single feature-level switch (never a per-tier or per-agent leaf); eligibility (`agents`) is a single org-level gate. A pre-flight validates working_hours agent/team names against the live roster (422 on unknown) before any write. Every working_hours write emits an audit row scoped to `config:working_hours` (who/when/before→after/tiers) via `AuditLogger.log_org_config_write` — reusing the established generic-scope-id convention on `audit_log.task_id` (no column change, no real-TASK-id overload). Validation is server-authoritative; the client does cheap format hints only. Routine-task editing is **read-only in MVP** (Phase 2 is the agent-contract-file write surface).

**Hot-reload:** Changes apply on next consumer read — dreaming scheduler picks up changes within ~1 min; threads/compose read on next request; session timeout applies to next session spawn. No daemon restart required.

The shipped frontend surface is `web/src/features/settings/SettingsPage.tsx` (left sub-nav + field panel per sub-route) with `lib/api/settings.ts`, `hooks/settings.ts`, and a `settings` domain in `DataContext`; the older `SettingsDialog.tsx` is unmounted retained test-only code.

### Agents page

The Agents page (`web/src/features/agents/`) shows the active agent roster plus pending enrollments. The mounted detail pane serves desktop and mobile; the retained drawer component shares the prompt editor without a new mount. Agent details include:

- **Repositories** — `repos` map from org/agents/<name>.md frontmatter (THR-095), shown as badge chips in the detail header.
- **System prompt** — collapsible, with Edit / Save / Cancel. The founder-only `PUT /agents/{agent_name}/system-prompt` accepts exactly `system_prompt` and the matching lowercase 64-hex `expected_revision` from `GET /agents`. Description remains read-only.
- **Model and reasoning effort** — editable per-agent selection beside Executor, with plain text, localized default, built-in token syntax and approved custom limits. CLI switches clear the draft pair. Locale changes preserve draft/focus/actions without a save.

Prompt saves preserve unrelated definition fields and normalize multiline/Unicode text through the existing parser and protected-input validators. The exact 200 receipt is `{agent, system_prompt, revision}` from one canonical byte snapshot. The editor freezes the draft base and captured org/target, disables typing/Save/Cancel through PUT and an explicit uncached GET, and shows Saved only when both body and revision match. Polling never rebases a dirty draft. Validated uncached prompt observations reconcile only the captured org/target prompt and revision in the roster cache, so leaving and returning preserves that observed base. Unrelated fields and agents stay unchanged; a newer prompt observed during readback wins and requires another explicit inspection instead of Saved. Ordinary reads already in flight are cancelled without reverting cached observations before reconciliation. Initial loading/error/empty/missing-revision/disappeared-target states refuse editing; close/selection/navigation discard locally and ignore late responses. en/zh-CN switching preserves the mounted draft, focus and selection. Errors retain the draft; inspect an explicit fresh read before deliberate reapply, with no automatic PUT retry. Cancelling the request can leave an admitted save committed: its started renderer and terminal reconciliation finish before the writer gate releases, so inspect fresh canonical state before reapplying.

Missing/malformed revisions return422 `expected_revision_required`; stale bases return409 `stale_agent_revision` with `current_revision`; absent active targets return404 `agent_not_found`. Reconciliation400 includes `system_prompt_reconciliation_failed`, a bounded diagnostic and actual canonical/workspace compensation statuses (`restored`, `not_owned`, `failed`, `not_required`). Audit500 reports `system_prompt_audit_failed` / `possibly_committed`, never an invented rollback. A canonical receipt does not prove workflow readiness or retained-provider adoption. Founder THR-280 seq21 waives C16 empirical genuine Claude/Codex/Pi/OpenCode adoption, including retained sessions and finite genuine Codex recovery episodes: FOUNDER-WAIVED / NOT EXECUTED, never PASS. Prompt delivery across launch/resume, all four adapters, full/delta/hole/fallback and finite callback-recovery orchestration remains required. Deterministic external CLI stubs can establish input transport/orchestration, not model adoption; THR-211 seq371 forbids real test-generated provider sessions in every venue.

Teams membership editing (add/remove workers only — manager reassignment is founder-gated) is available via `PUT /settings/teams`, wrapping `TeamsRegistry` mutators with `validate_team_membership` consistency checks and 409 rollback on drift.

Founder `happyranch teams ensure-default --org ORG` calls the bodyless
`POST /teams/ensure-default` to provision only an absent empty human-founder
Default. It preserves other teams, all existing workers and task routing;
compatible repeat is read-only, and conflicting management refuses409. The
JSON receipt includes created, team, both resolved pointers and authority.
HTTP200/CLI exit0 requires ready current authority readback; HTTP202/CLI exit1
means Default exists but publication/recovery remains incomplete. Repeating does
not repair authority. `happyranch teams list --org ORG` reads `GET /teams`.
The TS `ensureDefaultTeam` callable preserves the same explicit/partial contract;
it is not automatically mounted or sent. Add Agent already renders the returned
human team with Founder-managed labeling: refresh teams after provisioning and
select Default without creating an agent. An old running daemon lacks the new
capability; source acceptance/merge, loaded capability and live dropdown outcome
are distinct. The deferred offline consultant move is a separate operation;
no adoption/restart or agent-move authority follows from this command.

**Backend:** The `GET /agents` response now includes `repos`, `system_prompt`, and `model` fields (additive, `allow_rules` remains excluded). The `PUT /agents/{agent}/model` route sets or clears the per-agent model (see below).

The agent detail pane also reads `GET /agents/{agent}/cleanup-activity`. It returns at most five newest distinct own-agent tasks eligible by a historical same-agent `workspace_cleanup_triggered` audit or the exact manual first line `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` (alone, LF or CRLF; no substring/prefix matching). Eligibility and deduplication precede the five-row limit. Routine reports stay on this existing agent page through ordinary completion/results, with no cleanup-report thread creation, routine posting or reuse obligation. Historical threads/messages/associations/results/audits and explicit founder-requested coordination remain intact. Manual display does not increment daemon count or grant action authority. A task's lifecycle status and latest same-agent result status remain separate; missing summaries are rendered as unavailable. This GET is a read-only projection and does not start or perform cleanup.

Three nonunique cleanup indexes support the existing reader SQL and are installed
with `IF NOT EXISTS` after legacy columns exist, retaining existing definitions.
The synchronous route already runs in a worker. Breaker delivery listing and mint
now use sequential `await asyncio.to_thread` calls, so those DB waits run off the
event loop. Other callers can still wait for their own DB operation; synchronous
unload prechecks and close can still block the loop.

Build and dev commands:

```bash
scripts/build_web.sh
cd web && npm run dev
happyranch web
```

The SPA fetches the daemon bearer token once via `GET /api/v1/auth/bootstrap`, which is localhost-gated, then caches it in `sessionStorage` and attaches it to HTTP and SSE calls. CLI bearer-token behavior is unchanged.

## CLI

`happyranch cancel <task_id>` prints the daemon's cancelled-task summary, then
each successful cancellation-delivery record in response order. An opaque
control record without a PID prints `Cancellation delivered -> <task_id>
(<agent>)` on one line; it does not identify a signal or claim process closure.
A legacy PID-bearing record retains `SIGTERM -> <task_id> (<agent>, pid=<pid>)`.
An empty or absent delivery list retains `No live subprocesses attached.`

The CLI is an HTTP client. Start the daemon first.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
happyranch web [--no-open]
```

### Team-manager agent updates

`happyranch manage-agent update` is a task-session callback, not a browser
Agents-page feature. Read the active `GET /agents` roster first, compose the
intended whole-definition update from that exact row, and pass its `revision`
as `expected_revision` (or `--expected-revision` for the direct CLI form).
The daemon returns 422 for missing, null, or malformed revisions and 409 for a
stale base. On 409, reread and deliberately reapply the intended field change;
do not pair older composed content with a newer roster revision.

### Workflow template authoring (U1B)

U1B publishes inert immutable `product-design` template definitions; it does
not activate or execute them. An active, uniquely registered team manager uses
its verified task/session binding, and the server derives its organization,
principal and `org/<org>/team/<manager-team>` namespace:

```bash
happyranch workflows templates publish --org <org> --from-file /absolute/template.json --session-id <session-id>
```

Founder omits `--session-id`, supplies `team_slug` in the JSON payload, and the
command uses the existing daemon bearer. The payload contains
`operation_key`, `template_name`, `expected_current_version`, `definition`,
and (Founder only) `team_slug`; publisher, principal, namespace, org, task and
session claims are rejected. `--from-file` must be absolute. The legacy closed
definition is `kind=product-design`, with `schema_version` set to the genuine
JSON integer `1` (not a boolean, float, string or null), a Product Lead agent
author of an immutable PRD revision, Founder/implementer/tester reviewers, all
three required on the current revision, and request-changes returning to the
author. A non-empty description is the only variable descriptive field;
unknown fields and kinds fail closed.

New definitions use `kind=document-review`, genuine integer `schema_version=2`,
one agent author and 1–3 reviewers with unique abstract roles matching
`[a-z][a-z0-9-]{0,62}`, and at most one human reviewer. `output` contains
`primitive=immutable-document-revision` and a nonblank `description`. `outcomes`
contains `approved`, optionally `changes_requested`; `approval` is all/current
with required_roles exactly equal to reviewer order. `request_changes` is null
for approval-only, otherwise exactly action=return-to-author, revision=new,
invalidate=all-prior-receipts. `submission.timing` is on-completion or
while-active-or-completed. These are document capabilities, not execution grants.
Malformed new policy exits2 before either CLI client or port discovery; legacy
file/domain failures retain exit1. The server independently validates policy and
publisher authority. @2 pins are workflow-compiler@2/workflow-validator@2/operator-input@2.
All immutable versions remain readable by a compatible reader; earlier readers
refuse orgs containing @2 data, with no downgrade conversion promised.

Founder reads exact immutable versions with:

```bash
happyranch workflows templates list --org <org> --team <team> [--json]
happyranch workflows templates show --org <org> <team> <template-name> <version> [--json]
```

The matching APIs are `POST /api/v1/orgs/{slug}/workflows/templates/publish`,
`GET /api/v1/orgs/{slug}/workflows/templates?team_slug=...`, and
`GET /api/v1/orgs/{slug}/workflows/templates/{team_slug}/{template_name}/{version}`.
Responses return canonical JSON, base64 of the same UTF-8 bytes, SHA-256,
version/pins/timestamp and authenticated publisher provenance. Conflict,
stale-CAS, duplicate-content and authorization errors retain stable
machine-readable `detail.code` values; refusals leave no template residue.

### Workflow cutover

Founder bearer and actual org authority apply to all three methods:

```bash
happyranch workflows cutover show --org <org> [--json]
happyranch workflows cutover request --org <org> --from-file /absolute/request.json [--json]
happyranch workflows cutover downgrade-preflight --org <org> [--json]
```

The only request fields are `action` (`enable` or `disable`), an opaque ASCII
`operation_key` (1–128 characters from `[A-Za-z0-9._:-]`), and a strict positive
integer `expected_generation`. No client actor/org/owner/proof/verified/state or
receipt claim is accepted. Disable reason is fixed `founder_disable_requested`.
The API prefix is `/api/v1/orgs/{slug}`: GET `/workflows/cutover`, POST
`/workflows/cutover/requests`, GET `/workflows/cutover/downgrade-preflight`.
`workflowCutover.ts` mirrors these methods; there is no workflow UI page yet.

Responses expose stored marker/events and derived allowed actions, blockers,
actual owners, deferred actions, reconciliation_required and the reconstructed
`workflow-cutover-verifier@1` verification event. Requests add original request
identity/generation/action and replayed alongside current state. HTTP200 means
accepted, including truthful pending verification/drain. Retry an ambiguous
response with the same body/key; exact historical replay works after disable
and cold reopen. It never creates a new request identity.

Auth/org authority precedes body parsing; identity claims return403, strict
invalid input422, corrupt layout/history500, operation conflicts/stale CAS/
illegal edges409, precommit operational failure safe500. Errors expose only a
machine category. CLI exits0 for accepted reads/requests (including pending) or
eligible preflight, 1 for denied preflight/domain/input/transport failures, and
2 for usage. An existing F enable refuses before event/CAS writes with the
existing500 category `draft_schema_migration_required`; GET's existing blocker
`required_action` names `python scripts/migrate_workflow_draft_schema.py
--runtime-root <absolute-root> --org <slug>`. The operator runs that script
explicitly; `--check` exits3 when needed, 0 ready, 1 refusal, 2 parser. Existing F
history remains visible without recovery advancement. New `orgs init`/POST /orgs
creation initializes complete G directly. Preflight is read-only and permits only
pristine F initial history with no workflow data; every E/G needs a compatible
reader, including empty new/migrated orgs; even empty drained or template-only stores refuse.
The faf40744 reader accepts pristine F and refuses E/G; the b0b55e9f
reader accepts F/E and refuses G; older binaries
are not claimed to enforce the current cutover decision. Activation, first-draft
work and cancellation/dispatch remain later units; no live enable or deployment
is implied by these methods.

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands take no `--org`.

System Assistant is retired (THR-294). Its HTTP/WebSocket routes, CLI commands,
settings, dock and Cmd/Ctrl-K binding are removed. Existing assistant config,
conversations, workspace files and skill links remain inert and are not cleaned
or migrated. Provider conversation roles and ordinary org-agent capabilities
remain supported. The reserved `system_assistant` workspace name stays excluded
from the org-agent YAML migration, preventing legacy YAML reads or sentinel
writes. Historical database, audit and token records remain readable.

### Task work-status summary (TASK-5522)

`happyranch details <task_id>` prints a compact **Work status** block derived
server-side from the task record + audit rows (`runtime/daemon/work_status.py`)
and served on the existing task-detail envelope — no extra endpoint:

```
Work status: Stale-but-alive — no substantive update recorded
  Start:      2026-08-23 21:06:02
  Heartbeat:  2026-08-23 21:39:30 (fresh)
  Update:     No substantive update recorded
```

- The **Start** line is the current-session `session_start` audit timestamp
  (latest assigned-agent session; a prior session's receipts never count).
- The **Heartbeat** line is the last heartbeat with an explicit freshness
  suffix using the existing 60-second semantics (`fresh`/`stale`/
  `unavailable`) — it never claims execution progress.
- The **Update** line comes ONLY from a real `progress` audit receipt
  (timestamp + concise agent-written milestone). When none is in scope it
  prints the explicit `No substantive update recorded`; a live session whose
  start or last receipt is ≥ 5 minutes old renders `Stale-but-alive …`
  (policy `STALE_PROGRESS_AFTER_SECONDS = 300`).
- Terminal / pending / escalated / parked-on-block tasks print `Not
  applicable` with a reason — never an implied live agent.

The full audit log (including inline `progress` messages) is unchanged.

The former A-mode dock is retired (THR-294); no assistant HTTP or WebSocket
operation remains mounted.

### Org portability (Slice A)

CLI-only, relocation-only safety surfaces (no UI / TS client / browser
contract). Slice A is preflight + reconciliation only — it creates no archive
and performs no export/import.

```bash
# Read-only: classify every direct org-root child + report quiescence blockers
happyranch orgs portability-preflight <slug>

# Founder/master-bearer-only: reconcile exactly one confirmed zombie
happyranch orgs reconcile-portability <slug> --from-file /tmp/reconcile.json
```

`reconcile-portability` request JSON names one candidate plus evidence and a
disposition (`cancel` or `consume_result`):

```json
{"candidate_task_id": "TASK-123", "disposition": "cancel", "evidence": {"reason": "dead pid + stale heartbeat"}}
```

The `--from-file` path must be absolute. See `docs/agent-guides/features-and-invariants.md`
(Org Portability) and `docs/agent-guides/orchestrator-contracts.md` (Organization portability)
for the exhaustive root allow-list (including `work_hours`), quiescence/zombie
reporting, the conservative schedule policy (any armed or firing schedule
refuses, with existing-control remedies only), and reconciliation limits.

Full founder-facing CLI docs: `skills/happyranch/SKILL.md`.

### Memory report guard

The existing `/audit` read surface returns render-observed impression JSON with
`memory_telemetry_version=1`, `pointer_ids` and `full_body_ids` alongside the old
agent/session/digest/count/budget fields and unchanged actual task row scope.
Fit directives are full-body exposure; fallback directives are pointers; IDs
mentioned only in item text are excluded. No content is logged. Old writer
calls remain byte-equivalent and unversioned. Read/search routes and CLI hint
precedence stay unchanged; digest-first source resolution uses accurate item
IDs, allowing a genuinely nonshown search-result read to be search-sourced.
Only appended items with string IDs satisfying the existing `ID_RE.fullmatch` contribute identity metadata. Null, missing, nonstring or malformed IDs remain rendered byte-for-byte under the existing permissive parser, but contribute no identity or ID fragments from their representation, title or body. A malformed-only digest still launches normally and emits no impression; valid neighbors retain exact modes/counts. Strict writer validation still rejects malformed caller-supplied metadata before insertion.
A version is exposure metadata, never epoch authority or task eligibility.
The source-side G3 census records actual bootstrap intent/identity/expectation/
binding/launch-callback/terminal metadata plus independent seals in ordinary audit
rows. Existing `/audit` preserves entries/cursors and optionally adds
`memory_collection_observation` only for `action=memory_collection_seal`, from
the actual serving OrgState. Other action responses remain unchanged. The
closed source view and loaded identity are defined in the corrective memory
spec; absent/busy/moving/failed components are explicit unknown/unavailable.
GET performs no durable writes, resealing, provider launch or backend probe.
Epoch references are non-null only after G1 revalidates the original accepted
boundary and current full health. Missing, damaged, drifting or moving evidence
withholds those references; reads never append or repair collection state.
Stored seals checkpoint counters only: `census_integrity` explicitly reports
`census_not_reconciled` with `census_valid=false`. Exhaustive integrity is a
bounded internal zero-write validation with live semantic bookends; neither a
stored seal nor a valid source census establishes collection health.
G1 authority additionally reads exact admitted roles/results, original finite
plan/tasks, owned jobs/full outputs and operation history at stable bookends.
Its server admission, locked commit and serving checks use the existing
recorded-retry verifier; matching briefs or paired public audits cannot replace
raw supersession and invocation records. Synthetic closure includes every
verified retry and supporting root and must match the complete job-returned
set. Failed original probes still cannot supply successful canary evidence.
The seal view checks those bookends before validating the captured census;
measured movement returns `observation_moving` with null epoch references.
Independent installed/provider acceptance and operational health remain
separate from source implementation and source fixtures.

`happyranch memory report` paginates the existing audit read surface but is
fail-closed without valid G1 authority: JSON and text return
`insufficient_instrumentation`. Valid authority exposes the original epoch,
current health and eligible natural report; short samples remain insufficient.
There is no CLI flag or input that can override the invalid current/unversioned
epoch. Executor-owned task children receive their runtime session in the private
`HAPPYRANCH_RUNTIME_SESSION_ID` environment hint, so `memory get`/`search`
without a flag can preserve validated read-only attribution; a
nonempty `--session-id` still takes precedence; an explicitly empty flag retains
the existing environment fallback. Environment assembly with no runtime SID
sends an empty hint that overrides inherited ambient values. Ordinary/custom
execution forwards resolved invocation IDs; generated, unregistered IDs remain
uncredited. Provider resume IDs are never used for this purpose.
The report neither begins collection nor recommends push, alias, embedding, or
ranking changes.
The backend and canonical CLI share the pure `memory_telemetry_report` reducer.
They acquire complete bounded tasks and relevant memory/lifecycle/control audits,
including session starts/ends, runtime identity/launch/terminal and actual job
resumes, before any empty or short return. Backend acquisition uses a coherent
multi-table SELECT-only evidence snapshot. CLI exhausts `/tasks` pages at limit200
and relevant `/audit` action pages at limit5000 in two complete sweeps, comparing
tasks, audit identities/content and `/agents` roles at one aware UTC cutoff.
Task pages follow `(created_at, id)` descending; the last ID remains the transport
cursor resolved by the server. No-epoch diagnostic reads filter unrelated audit
actions without changing the100000-row bound. Accepted epochs retain complete
unfiltered task/result/job/audit acquisition, original outputs and serving bookends.
Events at or after that cutoff are excluded. Backdated/content/role drift,
cursor/schema errors, HTTP/decoder/timeout or SELECT failures refuse with
`acquisition_unavailable`; CLI writes only that category to stderr and exits1,
with no partial JSON. Unavailable `/agents` stays explicitly unknown and allows
safe descriptive counts; it never supplies functional cohort authority.

D exposes `by_session_kind` and `session_lifecycle` in JSON/text, with disjoint
`first_launch`, `job_resume`, `retry`, `retry_job_resume`, `other_continuation`
and `unknown` categories. Natural and excluded intervals first establish unique
SID-less end ownership; recovery remains excluded from natural counts and source
validation. Audit IDs order retirement even at tied timestamps. Any credited
read or search after end, terminal or a later natural same-task start closes
eligibility while valid earlier credits remain. Missing/ambiguous evidence yields
unknown/null hard-zero fields; absent kinds say `no_observed_population`, never
PASS. D-only reporting cannot repair A's unresolved live-foreign SID attribution.

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
flags may be true; without authenticated G1 authority, thresholds,
diagnostics_valid_for_collection and evaluation_candidate remain false,
decision `insufficient_instrumentation`. Probe freshness is initial/reset final-
commit admission only; reports authenticate that original boundary and current
full health. Equivalent replay never starts another day or renews proof.
`session_start` records audited intended invocations, never a complete process
launch/expectation census. G1 separately revalidates the full G3 census and
independent accepted probe/epoch evidence before natural eligibility. Report
reads never start epochs, perform tuning or ranking writes, or override authority.
The read-side snapshot/two-sweep contract detects observed drift; it adds no
writer fence or hostile same-UID guarantee. Actual installed canary acceptance
and operational H-v1 duties remain separately gated.

### PR CI wait / guarded merge entrypoints

Two CLI entrypoints (invoked as jobs or on task resume, not as `happyranch` subcommands) provide the PR CI polling and guarded-merge mechanisms:

```bash
# Poll job (submitted via happyranch jobs submit):
python -m runtime.daemon.pr_ci_waiter \
  --repo owner/repo --pr N --head-sha <40-char-sha> \
  --expected-check "Source Boundary Lint" \
  --expected-check "Web Lint (Node 24)" \
  --timeout-seconds 3600 --settle-seconds 120 --poll-interval-seconds 15

# Merge (triggered by resumed task):
python -m runtime.daemon.pr_ci_merge \
  --org <org-slug> --repo owner/repo --pr N --head-sha <40-char-sha> \
  --merge-method squash --ci-verdict ci_pass \
  --review-task-id TASK-xxx --qa-task-id TASK-yyy
```

For HappyRanch under THR-291 seq69, the two names above are unconditional on
both PR and exact-merge main. Daily Product E2E Health, manual Docs/lab and
release packaging are outside expected-check lists. Protection contexts/checks
are observed empty with strict=false, not enforced by these examples. First
scheduled E2E is PENDING; other repositories use their own policy.

Both print structured JSON verdicts to stdout and exit with mapped codes (0 = success).
The review/QA evidence extraction follows the **Merge-evidence contract** in
`docs/agent-guides/orchestrator-contracts.md`: the canonical vocabulary
`APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL`, NON-NULL structured
`verdict` primary (canonical token only), serialized `null` (the durable
recall producer's representation of legacy/no-structured rows) using the
strict annotated prose (`Verdict: PASS — rationale`) fallback, and fail-closed
rejection of missing/contradictory/malformed/ambiguous evidence and unusable
non-null structured values.
The poll job runs with `review_required=false` through the existing jobs path; agents never
get raw `gh pr merge` grants. The full workflow narrative (submit → blocked → resume → inspect →
merge/revise) is documented in `runtime/skills/bundled/jobs/SKILL.md` and
`docs/agent-guides/features-and-invariants.md`.

### Per-agent model and reasoning-effort selection

Built-ins accept case-preserving fullmatch `[A-Za-z][A-Za-z0-9_-]{0,31}` through shared `model_effort_args` validation; historical catalog values are metadata, not a membership gate. UI uses plain text: blank on explicit Save maps to null/default, without trimming or truncation. Explicit wire/direct empty strings refuse; CLI clear retains its existing meaning. Custom adapters still require approved declared-value membership (at most16 lowercase identifiers); legacy/model-only effort controls remain disabled with saved values visible. Token acceptance proves syntax/transport only, not native model/account support.

With explicit effort, OpenCode requires nonempty provider/model slash components (additional model path segments allowed), no whitespace/control/NUL, at most one # and a nonempty embedded variant byte-equal to effort; it composes one literal `-m provider/model#effort`. Unset preserves the original model transport. Pi recognizes only its existing finite thinking suffix set; arbitrary safe colon suffixes do not become native thinking syntax. Codex keeps one literal quoted config argv element.

Bounded evidence does not verify dynamic manage_agent update/enrollment at runtime (source only), native models/accounts, full task/thread/dream/wake/schedule lifecycles, host/callback or custom contained-wrapper prelaunch. General integration including callback is SKIPPED THR243seq42; former Python units are RETIRED. No ad hoc Product E2E or protected mutation/repetition proof is authorized.

Set or clear the model an agent uses via the `set-model` CLI command (mirrors `set-executor`):

```bash
# Set a model
happyranch set-model --org <org> dev_agent --model claude-sonnet-5

# Clear — revert to CLI default:
happyranch set-model --org <org> dev_agent
```

```bash
# Effort-only update preserves the selected model
happyranch set-model --org <org> dev_agent --reasoning-effort high
# Effort-only clear preserves model; cannot combine with --reasoning-effort
happyranch set-model --org <org> dev_agent --clear-reasoning-effort
```

The CLI with no model/effort options still clears model. The UI presents a plain text input; blank on explicit Save uses Default; a custom adapter without an input-v2 effort declaration is disabled with an English/zh-CN explanation. Unsupported levels and conflicts show localized errors and retain the draft; Save uses actual persistence and refetch. OpenCode requires a pinned `provider/model`, while existing `#variant` or Pi thinking suffixes are preserved when effort is unset and must agree when explicit. [Static mappings and custom opt-in](../superpowers/specs/2026-10-11-agent-reasoning-effort.md) describe transport only; account/model support and native OpenCode transport remain separate limits.

Model/effort drafts require the actual selected agent and successfully loaded executor metadata; loading, failed reads and empty lists remain distinct from a ready custom adapter without effort support. Saved values and failed drafts stay visible. Both the Save button and keyboard shortcut enforce readiness and prevent duplicate in-flight saves. A display placeholder never binds a request. Ready legacy/model-only adapters still allow model edits; repository-only edits do not depend on effort support. Refetch does not rebind an authored pair to a new executor: its original expected_executor still reaches the server stale409 check. Explicit executor selection resets the dependent draft, then executor and pair saves remain two separate writes.

The backend route is `PUT /api/v1/orgs/{slug}/agents/{agent_name}/model` with payload
`{"model": "<id>" | null, "reasoning_effort": "<level>" | null, "expected_executor": "<profile>"}`. Each selection member is optional: omission preserves it and null clears it. Invalid types/values or unsupported/conflicting effort return422 before canonical replacement. The UI binds each draft to its selected CLI; a saved CLI mismatch returns409 `reasoning_executor_changed`. It atomically updates the selection in org/agents/<name>.md frontmatter (THR-095).

When a model is set AND the executor profile has a `model_arg` template (all four built-in
profiles do — verified per CLI), the executor injects the substituted model flags into the
CLI argv at launch time. When unset, each CLI uses its own default model (today's behavior
for every existing agent).

Executor changes and model overrides are coupled: changing an existing
agent's executor clears its old configured model and effort, while selecting the already
configured executor preserves it. In the team-manager `manage-agent` update
contract, an explicitly supplied `model` (including `null`) is the model choice
for the new executor; if `model` is omitted during a real executor change, the
old override is cleared. The Agents web editor saves a dirty executor before a
separately dirty model and refetches the agent list after each mutation, so an
explicitly edited model is applied as the new choice and an untouched old model
is not restored from stale client state.

### Executor binary registration

Register the absolute path to each executor CLI binary so the daemon can locate it at
spawn time (THR-085). The daemon resolves binaries exclusively from the machine-local
``executors.json`` registry at launch — there is no PATH fallback (THR-107 seq155).
Registration is the sole availability gate; headless daemons and fresh machines must
have every executor explicitly registered.

**Exception — custom-adapter profiles:** profiles with
``command_adapter_id: custom-adapter:<id>`` use the exact founder-APPROVED,
hash-verified absolute adapter executable as their launch artifact — they do **not**
require a separate ``executors.json`` record keyed by their profile name.
``executors.json``.

```bash
# Register with explicit path (required):
happyranch executor-binaries register claude --path /opt/homebrew/bin/claude

# List all registered binaries:
happyranch executor-binaries list

# Conditionally remove a binary registration (kind + exact path must match):
happyranch executor-binaries remove <kind> --expected-path <absolute-path>
```

`--path` is **required** — omission does NOT fall back to PATH resolution
(THR-107 seq155). The operator must supply an explicit absolute path.

``happyranch executor-binaries remove`` atomically deletes a binary registration
when both ``kind`` and ``--expected-path`` match the stored record exactly:
200 prints the removed registration; 404 reports no registration; 409 reports a
stale observed path (refresh with ``list`` and retry); 422 reports validation/
built-in-protection/name-mismatch errors. This command removes machine-local
binary registrations only — it does **not** delete adapters, profiles, or other
daemon state.

**Custom-adapter lifecycle management (THR-107 slices 1–3).** The ordinary
Settings/onboarding "Connect a CLI → connect a custom CLI instead" flow is
now instant: the founder mints a token by naming the CLI — nothing else —
the candidate CLI's copy-pasted script both writes its wrapper at the
daemon-issued path and POSTs it directly, and the browser auto-finishes the
connection the moment it lands — no PENDING wait, no founder-approval
click, no separate Bind step. The wrapper's own `/connect` POST declares
its `workspace_adapter_id` (which workspace-bootstrap convention its
agents should use); the founder never picks this — only the wrapper author
knows which convention their CLI expects. Approved-unbound recovery
affordances, the pending-approval queue, and the standalone Bind card were
**removed from the ordinary UI** in slice 3 (not hidden behind an advanced
panel) — a connect that doesn't finish shows a retryable "Connection
failed" card instead.

Adapter-backed custom CLIs are surfaced inside **Settings → Executors →
Custom CLIs**: their approved executable is joined to the profile row by the
``command_adapter_id: custom-adapter:<id>`` reference. The authenticated
``DELETE /api/v1/runtime/adapters/{adapter_id}`` route remains available for
API-level cleanup — removal only deletes the durable registration entry
(never the on-disk executable) and writes an ``adapter_removed`` audit row
(scope ``adapter:<id>``).

The legacy ``POST /runtime/adapters/{id}/approve|reject|bind-profile`` routes
(and their ``happyranch``-adjacent TS bindings in ``web/src/lib/api/
adapters.ts``) are preserved, unchanged, as **operator-only one-time
disposition tooling** — no normal-flow UI calls them anymore
(``tests/contract/route-classification.json`` reclassifies them as excluded,
no browser consumer). Reach for them only for manual/scripted recovery of a
legacy PENDING/approved-unbound record; a new custom CLI should always use
the ordinary Connect flow instead.

### Token usage

`happyranch tokens` shows `session_token_usage`. Default is the most recent rows;
a `--by-*` flag (mutually exclusive) switches to a rollup:

```bash
happyranch tokens --by-agent | --by-task | --by-thread | --by-purpose
```

`--by-purpose` groups by `invocation_purpose` (route `group_by=purpose`). Filters
(`--since`, `--thread-id`, `--agent`, `--purpose`, `--scope-type`,
`--scope-id`, `--task-id`) AND-compose with any view.

Usage v1 efficiency cohorts do not derive historical executor/model from this
usage view or from current agent configuration. Lifecycle records capture the
effective launch tuple: thread invocations use nullable executor/model columns,
task `session_start` adds runtime session ID + actual spawn purpose +
executor/model, and `dream_started` adds executor/model. NULL remains unknown;
there is no historical inference or `"default"` sentinel.

The read-only Usage v1 API is `GET /usage/workload?compare=bool` and
`GET /usage/efficiency?compare=bool`. Efficiency without a cohort returns
options and unattributed counts; a selected cohort requires `executor` plus
exactly one of `model` or `model_unpinned=true`. Both routes use the same
bearer authentication and org scoping as `GET /tokens`. They return rolling
seven-day UTC bounds with the resolved org timezone for display. Workload
emits only current-window agents. Each period's
`reply_outcome_coverage` reports linked `recorded` replies out of
`total_consumed` REPLY wakes; a NULL `reply_message_seq` is unknown, does not
count as a Reply, and withholds only the Replies delta when comparison is on.


### Usage presentation locales (THR-118 W4d-2)

The Usage v1 page translates app-owned Workload/Efficiency labels, columns,
run types, statuses, comparison/coverage explanations, help, retry/stale/empty
copy, ARIA, counts and units through `usage.*`. Raw agents, CLIs, model names,
authored values and timezone identifiers remain verbatim. Window `*_local`
parts already describe org-local wall-clock time and never shift with viewer
TZ; UTC instants use the response timezone and explicit locale. Invalid-format
fallbacks keep their original behavior. Compare/cohort/default/manual selection
and all query/refetch/server metric semantics are unchanged; locale changes
preserve nodes, focus and selection without new API calls. Usage is translated. W5b enables full browser-language defaults; native N0/N1 remain deferred. Browser evidence:
`web/scripts/w4a-browser-evidence.mjs --slice usage` (ordinary bundle).

The web Usage page (`/orgs/<slug>/usage`, `web/src/features/usage/`) is the
Usage v1 UI over these two routes and no longer reads `GET /tokens`. It shows
a fixed "Last 7 days" window taken from the response (with Data through and
generated-at in the response timezone) and a Compare toggle that is off by
default. Workload lists exactly the returned agents: Task runs, Thread wakes,
Recorded runtime (agent runtime with known/total coverage; missing runtime is
"Not recorded", never zero), Deliveries (unclassified results are footnoted),
and Replies (recorded X of Y, with the unknown portion labelled "reply outcome
not recorded"). Efficiency renders nothing until exactly one CLI and then one
model are chosen from `cohorts`; the NULL-model cohort is its own "CLI default
(not pinned)" option, and there is no aggregate option. The five run-type rows
always render in fixed order. A null class reads "Not reported", or "Unknown"
when no run in the row has usage, and is never zero. A partial Fresh input is
labelled with its class denominator, and decline waste appears only on the two
thread rows. Unattributed lifecycle counts are listed outside the rows. Every
delta is rendered from the server's `UsageDelta`, with neutral styling. A
withheld delta shows a dash, both periods' coverage and a plain-language
reason, never the raw code. Loading, per-view error with retry, stale (earlier
figures kept after a failed refetch), and empty states are explicit. Below
`md` both tables scroll sideways under a frozen identity column. There is no
export, cost, or blended token total.

Rollup modifiers (presentation-side; require a `--by-*` flag):

- `--top N` — rank by churn (`total`) DESC and keep the top N; ties: sessions DESC then key ASC.
- `--over-threshold N` — keep only groups whose churn strictly exceeds N (applied **before** `--top`); empty result prints a "nothing would alert" line.

**Churn invariant:** `total = input + output + reasoning`. `CacheR`
(cache reads) rides in its own column and is **never** folded into `total`
or used to sort/threshold — it overstates burn ~10–100×.

The `--by-agent`/`--by-thread` rollups add a **Model** column
(none on `--by-task`/`--by-purpose`). Its label is classified at render time —
a single presentation constant `MODEL_FIX_CUTOVER_TS` draws the pre/post line,
never SQL:

| Label | Meaning |
| --- | --- |
| `<model-id>` | one observed model |
| `(mixed)` | >1 model, or observed+NULL mixed, or NULL spanning codex+claude |
| `(cli-unreported)` | all-NULL codex (codex emits no model field) |
| `(unknown — pre-fix)` | all-NULL claude, all before the cutover (frozen history) |
| `(unknown — ANOMALY)` | all-NULL claude, any at/after the cutover (parser-drift canary) |

The founder dashboard carries a read-only **Top token threads** card (a
window selector for 24h/7d/30d) backed by the same `/tokens?group_by=thread`
route. It ranks threads by churn (`total`) DESC client-side, shows cache reads
as a muted secondary number (never in the bar or the rank), and labels each
thread's Model with the same precedence as the CLI table above.

### Staged dual-text escalation-policy editor (THR-229, draft PR878)

This surface is present only on the unmerged draft feature branch; it is not a
running-production capability and performs no live activation. Every roster-
confirmed manager with exactly one matching team registration has a dedicated
policy route showing exactly the editable
`What to escalate` and `What not to escalate` textareas. A genuinely empty
selector begins with the server-projected neutral starter bytes and uniform
per-team derived ID/title; there is no client starter constant or Engineering
special case. An active v2 selector reads
both values and its release/version/digest, activation, selector and epoch from
the authenticated projection. One confirmation sends both texts through the
existing paired v2 release transaction. It never exposes the legacy split
create/activate sequence, a one-text save, clause IDs, continuation phrases,
rollback controls or a second evaluator.

The client keeps a dirty pair across ordinary query renders and guards both
SPA navigation and hard unload. It treats pending submission as single-flight,
accepts success only after the paired receipt and authoritative refetch agree,
reuses the exact request after an ambiguous outcome, and preserves the draft
on selector conflicts until the operator deliberately reloads the current
selector. Policy queries wait for both roster and team data, are keyed by the
exact organization/manager/team tuple, and are evicted immediately on tuple loss
or change to prevent cross-team flashes. The v2 history stream is immutable and read-only, shows both exact
texts plus full release/activation/selector identity and timestamps, and owns
pagination/error retry independently. The legacy history and outcome APIs/data
remain available, but the two legacy read-only UI sections and their eager
requests are removed. Unsupported, mixed, corrupt,
unknown and worker targets fail closed without an editable fallback.

For a v2-bound manager escalation, the injected role guidance supplies the
binding identities and requires one structured `manager_self_evaluation` beside
the ordinary `decision`. The active policy block is authoritative for its exact
object shape and supplies a filled launch-bound example in that manager-decision
task; managers set both assessments honestly rather than treating the displayed
values as defaults. Policy blocks rendered for thread, wake, schedule, and dream
contexts have no bound manager-decision identity and show shape/type placeholders
only, not values to submit. A manager self-evaluation is submitted only from the
manager-decision task's report-completion using that task's bound root/session
identities.
`happyranch report-completion --from-file` preserves that member for strict
server validation; omission, explicit null, malformed or uncertain evidence
fails closed. The CLI does not embed policy prose, a clause identifier, a
canonical continuation phrase, or a second evaluator. Worker and ordinary
non-escalation callbacks omit this field unless their injected contract explicitly
requires it.

The checked-in browser receipt driver is
`web/scripts/screenshot-harness/shot-thr229-v2-policy.mjs`. It uses an owned
runtime/database, loopback daemon and Vite proxy with real bootstrap auth; it
proves empty bootstrap, paired persistence and full-reload readback/history, a
two-text atomic rollback at the control-audit boundary followed by exact retry,
invalid-token 401 with no row, and worker 404 with no editor. This checkpoint
does not close the combined THR-229 feature, final parity, review/QA, merge or
rollout gates. Source merge is not activation: compatible daemon, CLI, and
manager-launch code must be deployed and old launch/completion consumers drained
before any future production save/activation. Rollback is compatible-code-only;
this candidate performs no deployment, save, activation, or natural production
continuation.

## Agent-Side Callbacks

These are invoked by skills inside agent sessions. Do not invoke them by hand; doing so falsifies audit data.

- `happyranch report-completion`
- `happyranch progress`
- `happyranch memory {add,update,promote,reindex}`
- `happyranch manage-agent`
- `happyranch manage-repo`
- `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

Callbacks should use `--from-file <path>` where payloads have multiple fields. **The path MUST be absolute** (e.g. `/tmp/completion.json`). A relative path silently resolves against the agent's cwd and can litter stray files under the runtime orgs root. The CLI rejects relative paths with a clear error in the callback family (`report-completion`, `threads reply/decline/dispatch/compose`). See `docs/agent-guides/agent-executors-and-permissions.md`.

A successful `threads decline` prints `ok: declined on <thread_id>` using the
returned thread ID. Silent declines return status `declined` without a message
sequence because they create no transcript row. Callback errors retain the
existing nonzero exit and error output.

### Retired System Assistant

System Assistant is retired (THR-294). Its HTTP/WebSocket routes, CLI commands,
settings, dock and Cmd/Ctrl-K binding are removed. Existing assistant config,
conversations, workspace files and skill links remain inert and are not cleaned
or migrated. Provider conversation roles and ordinary org-agent capabilities
remain supported. The reserved `system_assistant` workspace name stays excluded
from the org-agent YAML migration, preventing legacy YAML reads or sentinel
writes. Historical database, audit and token records remain readable.

The finite retirement browser selection runs against the ordinary compiled SPA
with synthetic API fixtures, and writes DOM, keyboard, HTTP/transport and image
receipts for both locales at 390x844 and 1440x900:

```bash
cd web
node scripts/w4a-browser-evidence.mjs --dist "$PWD/dist" --out <evidence-dir> --head <exact-head> --slice assistant-retirement
```

Default/legacy/unknown Settings paths resolve to Capacity before its data gate;
the four remaining sections, Preferences, header language and theme controls
remain usable. These browser fixtures supply no real backend route, daemon or
installed/frozen readiness proof. The browser driver bounds CDP commands and
writes an explicitly incomplete receipt on failure.

### Founder initial activation and draft receipts (S2)

The existing human bearer boundary proves the bearer performed the action, not
that separate humans hold separate credentials. No task/session fallback exists.
Manager template publication alone does not activate or assign work.

```bash
happyranch workflows activate --org <org> --from-file /absolute/activation.json [--json]
happyranch workflows activations list --org <org> [--json]
happyranch workflows activations show <activation-id> --org <org> [--json]
```

The API has exactly POST and GET `/api/v1/orgs/{slug}/workflows/activations`
and GET `/api/v1/orgs/{slug}/workflows/activations/{activation_id}`. New creation
returns201; identical actor/org/key/request replay returns200 with the same
original root/intent/pins/time. List returns an array; show returns one receipt.
CLI parser/local malformed-file errors exit2, domain/transport errors exit1,
and valid receipts, including pending execution, exit0. Retry response loss with
the same immutable request/key. No reactivation, reassignment or retry mutation
endpoint is supplied by S2. The typed API mirror is `workflowActivations`. The served
OpenAPI request discriminator maps both task-attachment and thread-attachment
inputs to their actual inlined variants in the served document; those pointers
are part of the contract keeper.

Every request object and nested record is closed; unknown fields, booleans in
integer fields, identity/provenance claims and `latest` aliases refuse. Legacy untagged requests retain exactly four fixed roles and unchanged wire
bytes. New requests add `format=workflow-activation-request@2` and bind exactly
the selected immutable template roles; new receipts add
`format=workflow-activation-receipt@2`. Human slots bind Founder/founder/null with
empty replacements, agent slots bind distinct active same-org canonical members.
Agent-only templates have zero human aliases. Replacement maps have the same
exact slot keys and at most16 candidates per agent slot. Recipients are1–4 unique
abstract roles from that template. Unknown format/role syntax/duplicate recipients
refuse locally; exact template membership remains server-owned. Requests cannot
add return-to-author to a null-return template. The served request/receipt unions
and input discriminator pointers cover both formats. Concrete legacy
request fields are:

| Field | Contract |
| --- | --- |
| `operation_key` | Nonempty bounded operation key; never reminted on retry. |
| `instance_id` | Caller reference, lower-case slug beginning with a letter; never a task ID. Identity is org plus this reference. |
| `expected_activation_revision` | Integer0; initial admission only. |
| `template` | Exact `identity_id`, positive integer `version`, 64 lower-case hex `definition_digest`. |
| `authority` | Current coherent `namespace`, positive integer `generation`, 64 lower-case hex `snapshot_digest`. |
| `scope` | Nonempty bounded `brief` describing the document task. |
| `bindings` | Exactly `product-lead`, `founder`, `implementer`, `tester`. Each has `kind` (agent/human), `principal`, and nullable `team`. Founder is human/founder/null; other roles require active same-org canonical agents/teams and independent principals. |
| `eligible_replacements` | The same four role keys with explicit arrays of bindings; Founder array is empty. These are inspectable candidates, not permission to replace. |
| `allowed_actions` | Unique closed values: `draft-document`, `submit-immutable-document`, `collect-review`, `approve-planning-input`, `return-to-author`; draft-document is required. Later actions do not enable deferred producers. |
| `inputs` | Bounded array of same-org source pins. `task-attachment` has `task_id`, `storage_key`, `sha256`, `recipients`; `thread-attachment` has `thread_id`, `attachment_id`, `sha256`, `recipients`. Recipients are explicit role names. Existing visibility owners govern access; private thread inputs cannot expand participation. No host path/URL or mutable-only source reference. |

Server snapshots inputs and bounds total canonical context to1MiB. Source digest
mismatch/unavailable source returns422 with actual owner and required_action.
No private context bytes are returned in receipts or errors. No new input
entitlement follows from being selected for a role.

Each receipt carries immutable original fields `activation_id`, `instance_id`,
`instance_reference`, `activation_revision`, `root_task_id`, `intent_id`, exact
`template` (the request pin plus `version_id`, `compiler_pin`, `validator_pin`,
`source_pin`), `authority`, `bindings`, `eligible_replacements`, `allowed_actions`,
`scope_digest`, `context_digest`, `activated_by` (principal_kind human,
principal_id founder, proof_kind founder_bearer), `created_at` and
`original_request_digest`. `replayed` distinguishes exact POST replay. The separate
current projection has `state` (queued/claimed/running/uncertain/cancelled/failed/
completed), `execution_started`, `pending`, `reconciliation_required`,
`cancellation_requested`, `current_eligibility` (`eligible`, blocker code array),
and `responsible_owner`. Session registration does not set execution_started;
a genuine bound host handle does. Missing callback/ack/quiescence remains pending
uncertainty; successful transport alone is not draft completion. A completed
draft is not immutable submission or approval. Template v2 publication/reopen
cannot retarget original v1 pins. No bearer or opaque host-control token is exposed.

Safe errors retain `workflow_activation_operation_conflict`,
`workflow_activation_cas_stale`, `workflow_activation_authority_stale` (409),
`role_binding_not_authorized` (403), bounded invalid/unavailable input (422),
and `workflow_activation_storage_corrupt` (500, no repair). Existing human_only,
authority/profile and OrgDep errors remain unchanged. Disabled/busy/fenced
admission creates no activation/task and is distinct from a persisted queued or
uncertain instance. Independent review/QA/CI, U3-U6, mounted UI and separate
Founder Request changes/Sign off/operator observations remain acceptance gates.

### Thread list counts and infinite loading (THR-289)

The Web inbox reads one active Open, Archived or All stream in 50-row pages. Header/bucket/dream counts come from server totals, remain unknown before the first successful read, and show recorded values with updating/stale state during refresh/failure. All uses one global order. Search retains the literal case-insensitive subject-or-ID predicate; whitespace-only disables it, while every other filter serially exhausts the stream even when early matches fill the viewport. The ContentWrap sentinel and accessible Load more share one request latch. A failed continuation leaves rows navigable and pauses automatic demand until Retry.

Refresh restarts at a null cursor, privately stages a prefix to prior depth and commits it together. Org/bucket/generation ownership and abort signals reject late results. Inbox reconnect/events, focus and local thread mutations reconcile rows and totals. Pin freezes paging, snapshots actual legacy/infinite/detail caches, reorders loaded Open rows only, conditionally rolls back owned writes and restarts authoritatively. Scoped scroll offset/anchor/depth survives detail/back; restoration waits for enough content or termination and retains its target through errors. User movement cancels pending restoration. Command palette reads only committed active-org caches without fetching. CLI calls retain the existing omitted-pagination contract.

### Manual local-CI receipt contract

The nightly workflow's default manual lane takes `all_only` (boolean, default
false); there is no `run_integration` toggle. Explicit `naming_only=true` with
`all_only=false`, exact published `naming_source_sha` and immutable amd64
`naming_image` suppresses the broad manual lane and selects only finite naming.
The default lane invokes the extracted receipt-producing
`uv run python scripts/nightly_local_ci_all.py` runner from the checkout root,
which records actual checkout/source/tool provenance and the
`scripts/local_ci.sh all` command exit. The manual cap is the approved 150
minutes; general integration retains its 30-minute cap and exact schedule-only
predicate, so all three manual input cases skip it. THR-291 seq40 retired
Python unit source, G named-case controls/repetitions and the PR1011 unit entry.
The wrapper verifies source boundary and Web static checks only under seq69,
with units RETIRED and first daily E2E PENDING. General integration remains SKIPPED under THR243 seq42, never PASS.
See `docs/local-ci.md` for current checks and selectors.

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
current names and permanent IDs. Selected values stay IDs across renames. Thread-detail
composer @mention hints offer only eligible participant agents, including their
current Name · ID labels and permanent-ID matching; human Founder is never
suggested, even after renaming. Manually typed current Founder names and
`@founder` remain literal and retain existing preflight/send behavior. Human
recipient controls elsewhere still offer the separate Founder option. Clear
invalidates pending task-filter resolution; stale success/error/finalization cannot
reapply a cleared filter, show an obsolete alert or release a newer attempt. Founder
is a separate human option routed as established `@founder`, never an agent
participant or task owner. There is no task-create owner GUI in the present web
source; the existing task agent filter is the relevant selector. Prospective
invite resolve omits `thread_id` because that optional scope means existing
participants; the actual invite endpoint retains final action eligibility.
Recognized former body/recipient tokens stop UI-managed associated attachment
uploads when caught by separate preflight. Unknown body @text remains literal,
including the existing quoted/email token parser behavior. This read is not a
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

### OpenAPI snapshot operator

`uv run python scripts/generate_openapi_snapshot.py --check` (also the default)
compares exact summary bytes without writing or starting a daemon lifespan.
After review, `uv run python scripts/generate_openapi_snapshot.py --write`
atomically replaces `tests/contract/openapi.json`. The summarizer preserves workflow input discriminators. The former Python
contract test is retired; Web/Swift still consume the snapshot and route
classification. This operator does not run tests.


## Root task Pause and Resume (THR-292)

Use `happyranch pause TASK --org ORG`, `resume TASK --org ORG` and `tasks --org ORG --pause-overview`. Controls read the generation and POST once unless --expected-generation is supplied; stale conflicts are not retried. --json preserves the separate pause projection; details supports --json. Overview requires explicit --org, uses bounded root pages (--all-pages follows them), reports unavailable counts honestly and never promises a safe restart. API POST /tasks/{task_id}/pause|resume accepts only expected_generation; GET /tasks/pause-overview precedes dynamic task routes. TS task client/types and OpenAPI/classification share this contract. All rendered pause UI lives on Task detail only: founder THR-292 seq41 removed the former standalone Task pause overview panel and seq45 removed every pause control, state label, drain evidence, blocker list, unavailable message and inherited-root explanation from Tasks list rows, so the list performs no overview read and shows only ordinary lifecycle, navigation and lineage (the JSON/API list projection still carries `pause`; use the CLI/API overview). Task detail adds bilingual root-only Pause/Resume in its existing header action row (founder THR-292 seq77): an unheld root shows only a compact Pause beside Revisit/Cancel and the audit link, with no unheld label, drain evidence or timestamp; Pausing shows an amber "Waiting for current work to finish" indicator with secondary Resume and a default-collapsed native disclosure whose rows link the actual task/job IDs and keep raw owner_kind/reason verbatim; Paused shows a separate Paused indicator with primary Resume. The disclosure summary counts only task_session/job blockers as "still running" and reports launch/result_processing/unknown as other items to resolve plus incomplete evidence; the projection observation time and incomplete-evidence notice stay inside the disclosure, which also carries terminal/inherited drain diagnostics. Mobile document order is indicator/Resume, disclosure, then ordinary actions/audit; desktop keeps one row with the disclosure below. Pending roots have no Pause; parked in_progress roots can Pause. Descendant detail shows inherited hold and the actual root link. Escalated/terminal lifecycle and waiting/attention/lineage remain separate; terminal drain diagnostics stay visible and Resume never resolves escalation or revives a terminal task. Missing projection or unsafe/missing generation is unavailable. Each deliberate action uses the displayed generation once in the task provider; repeated submissions are disabled while pending, conflicts/refusals are accessible and translated, and the provider refreshes the originating org's list families and all details without automatically replaying or opposing the action. The shared HTTP client's existing401 bootstrap/retry is unchanged and is authentication recovery, not Pause enforcement evidence. CLI/API overview pages carry server timestamps, evidence_complete and nullable counts; a next page replaces the observation, never a summed completeness/zero claim. Other roots/thread/dream/wake/scheduled activity and eligible manager-created separate unheld successor roots stay independent; this is never a safe-restart certification. Prototype controls refuse as unavailable. The task-owned pause browser harness records ordinary-build UI/transport requests and bilingual responsive/keyboard evidence without a live daemon, including list absence of all pause UI and the overview panel, zero overview reads, and detail-only actions/refusals/locale focus. [Contract](../superpowers/specs/2026-10-10-root-task-pause-resume.md).


### U3a(c) public handoff and unsigned request surfaces

The U3a(c) historical checkpoint initially served POST `/workflows/submissions/handoffs`, GET
`/workflows/submissions/handoffs/{handoff_id}` and GET
`/workflows/reviews/requests/{request_id}`, under `/api/v1/orgs/{slug}`.
Matched `@3` activation request/receipt schemas coexist with historical `@1/@2`.
Publication `@3` requires closed submission={timing,delivery}, with delivery
`current-author-handoff@1` and timing `on-completion` or `while-active-or-completed`;
historical `@2` remains timing-only.
An original CURRENT author supplies `session_id` and one closed
`workflow-author-handoff-command@1` JSON body containing operation_key,
activation_id, expected_revision=1, submission_bytes_base64 and submission_digest.
The command is bounded to 2MiB; the exact strict UTF8 document is 1..1MiB.
Stage commits201; exact before-callback replay returns200. Every post-callback
POST refuses409 source_not_current; after tracker clearing the session refusal
precedes replay. Staging returns no graph, launch, signature or approval.

Handoff GET chooses exactly one current proof: session_id+activation_id for the
original author; session_id+request_id for the selected actual reviewer; or
proof_kind=owner-human for the current Founder owner, including zero-human-review
configurations. Request GET uses session_id for the actual agent recipient or
proof_kind=human for the selected human recipient. Unknown, duplicate, blank,
malformed or mixed selectors refuse422. Agent lanes are header-free;
a valid bearer conflicts403 auth_path_conflict, and an invalid present header
refuses403 bearer_not_accepted. Human lanes retain the existing Founder bearer.
Selection/current membership/grants are revalidated: invisible scope404, selected
grant loss403, unready current authority409, stored corruption500. Present-H
reads select relational source/request/owner scope and the current grant before
decoding H admission/graph proof; admitted reads retain complete validation in
the authority writer and binding lease. Missing-H guidance follows independent
identity checks. Completed-handoff discovery keeps a normally revoked or
superseded reservation held individually and continues to independently
revalidate later reservations; stored corruption still refuses the scan.

The current Founder owner can read a superseded source with the SAME active
authorization pointer and collect-review grant, full immutable ownership closure
and coherent current authority; no human reviewer row is required. Source and
request agent selection remains strict. An original-current source GET after
callback and before tracker clear is allowed; tracker clear refuses the session.
After valid admission, corruption refuses500 and genuine failed/cancelled sources
retain their actual reconciliation flags. Otherwise disabled cutover takes
precedence over supersession, authority change and uncertainty:
held/new_runs_disabled, then held/source_not_current, then
held/authority_not_current, then uncertain/missing proof and eligible graph or
callback states. Every held graph is null, with the same immutable core and no
domain/queue/launch effects. Historical owner reads grant no POST/consume authority.

H receipts contain exactly format/core/replayed/current. Core has only
handoff_id, reserved_submission_id, instance_id, revision, candidate_digest,
request_digest and created_at. Current contains state, pending,
reconciliation_required, reason and graph; graph is null unless currently
consumed-authorized and then holds submission_id, round_id, request_ids and every
actual configured agent reviewer_task_id in declared role order (0..3).
Private source, admission, result, host, capability and input bytes never enter
an H receipt. Request receipts expose the pinned document and only the selected recipient's
inputs. U3b(d) derives real pending/signed status and the safe selected receipt;
the immutable scope purpose literal still says unsigned and is never rewritten.
Callbacks preserve actual execution evidence and never sign a review.

CLI commands are `workflows submissions stage --org ORG --session-id SID
--from-file /absolute/command.json [--json]`, `workflows submissions show H
--org ORG --session-id SID --activation-id A` (or --request-id R), and
`workflows submissions show H --org ORG --proof-kind owner-human`.
`workflows submissions request-show R --org ORG` requires either --session-id SID
or --proof-kind human. Every command forwards the exact payload/query once;
syntax/unknown fields exit2, domain/transport failure exits1, pending/held/success
exits0. Lost responses retain the original key; no automatic operation replay.

TypeScript exports stageAuthorHandoff/getAuthorHandoff/getWorkflowReviewRequest
through the unchanged shared client.ts. Bearer-sending agent TS calls expect403;
owner reads can return200. One logical operation uses one endpoint fetch, with
only the existing generic first401 token refresh permitting a second fetch of
the same URL/body/key. Domain403/409 and response loss do not replay operations.
Method/path classification includes the common GETs and U3b(d) human POST, and
excludes the agent handoff POST. SurfaceAllowList denies that exact normalized/prefixed POST, adding no GET
grant or broader permission change. Actual operator UI, revisions/reassignment/recovery and independent acceptance
remain later units; genuine receipts/joins remain dependency-incomplete;
the manager-owned same-runtime two-template public smoke is required before
Founder invitation. Source parity is not behavioral verification.



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
open naming PR; each corrected source requires its own attributed verification.
Its ordinary selection is exactly 20 function selectors / 23 parameterized cases
for the same twelve scenarios, including C1/four C2/F1. Named daemon receipts,
complete full-definition/CLI/readback evidence and every selected JUnit outcome
are required. The exact offline workload container contains nested CLI sessions;
its stopped ID/state closes that boundary. A SHA256/byte inventory authenticates
complete export before owned storage cleanup; uncertain closure/export preserves
partial receipts and exact storage names, subject to the original holder deadline.
Founder THR293seq143 excludes all 17 additional defect/restoration/repetition
proofs including O6 from naming merge conditions; they remain UNPROVEN, never
test-authoring PASS. Independent full review, separate current-candidate behavioral
QA and the existing lint/hooks/guarded merge/post-main gates still apply. The
runner preserves source/evidence sentinels through init/prepare/work/export with
a bounded offline mount holder, refuses output truncation, and reserves cleanup
under one job-start deadline. Python unit retirement and
general integration exception retain their exact scope; no behavioral acceptance,
merge, live migration or deployment is established.

### U3b(d) human write and selected signed request

POST `/api/v1/orgs/{slug}/workflows/reviews/human-receipts` uses the existing
Founder bearer with no query proof or body principal. The body is exactly
`workflow-human-review-command@1`, action `sign-review`, operation_key/request_id
tokens (1..128 `[A-Za-z0-9._:-]`), strict integer expected_revision=1, lowercase
SHA256 request_scope_digest, declared outcome approved or changes_requested,
canonical findings_bytes_base64 (4..1398104 characters, 1..1048576 decoded UTF8
bytes) and lowercase findings_digest. The whole command is <=2097152 bytes.
The existing guarded writer revalidates current authority/recipient/contributor
independence and owns collect-all. Exact current actor/key/full-payload retry
returns HTTP200 with the original immutable receipt; changed payload/key or
cross-request key reuse conflicts409. First response preserves peers. Agent
labels cannot select this lane; wrong pinned kind/nonrecipient is invisible404.

The closed response is `{format:workflow-human-review-receipt@1,receipt}`.
Receipt has exactly format=workflow-review-receipt@1, receipt_id, request_id,
submission_id, submission_digest, assignment_generation=1, request_scope_digest,
outcome, findings_bytes_base64, findings_digest and original created_at (1..64
characters). No private proof/pins/admission, unrelated inputs, peer findings or
credentials are returned. GET `/workflows/reviews/requests/{request_id}` retains
exactly session_id OR proof_kind=human. Full genuine receipt/graph closure follows
current recipient/session/grant admission, with binding lease and after-wait
checks. Pending returns status=pending/signed=false/pending=true/receipt=null;
signed returns status=approved|changes_requested/signed=true/pending=false and
the safe receipt. Reconciliation remains an independent current flag. Expired
reviewer sessions cannot read signed history. No owner audience is added.
The saved unsigned-purpose text and stored brief bytes stay immutable.

Malformed/oversized/duplicate/unknown/mixed selectors or JSON refuse422; body
identity/proof fields refuse403. Domain conflicts409, grant/contributor/auth
refusals403, invisible request404 and category-only storage failures500 are
distinct. Existing human_only detail and authority/schema owner guidance remain.
After actual selected-human/current-grant admission, missing retained template
mapping or activation closure and malformed retained activation requests yield
only `workflow_review_receipt_storage_corrupt`500. These dependency failures
do not become caller422 or invisible404; current-after-wait checks still apply.
CLI `happyranch workflows reviews sign --org ORG --from-file /absolute/command.json
[--json]` uses Founder transport and forwards the retained command once.
Existing `workflows submissions request-show R --org ORG --proof-kind human`
(or --session-id SID) prints actual status/signed/pending. Syntax/file errors
exit2, domain/transport errors exit1, success exit0. No new automatic retry.
Shared `workflowReviews.signHumanReview` and getWorkflowReviewRequest plus all
command/receipt/error types export through the existing api index. client.ts
retains the sole generic first401 refresh; 403/409/response loss cannot invent
a new operation. Static classification includes the human POST. Reviewer staging adds only the native denial described above; direct-submit
remains deferred and no new remote reachability follows. All behavioral/proof results UNEXECUTED under
429/430; source parity is not acceptance, and root public smoke465 remains required.

### Current-reviewer stage transport (seq475/493)

Transport403 (valid bearer auth_path_conflict; any other Authorization header
bearer_not_accepted) precedes query422. Exactly one nonblank session_id token
[A-Za-z0-9._:-]{1,128} is required; mixed/unknown/duplicate selectors refuse.
Stream body is 1..2097152 bytes; UTF8 JSON object only, no BOM, duplicate keys
at any depth, nonfinite numbers or unknown command fields. Decoding422 precedes
decodable body-identity403; closed command422 follows. Allowed fields are format,
action=stage-review, operation_key, request_id, expected_revision (strict INTEGER1),
request_scope_digest, outcome, findings_bytes_base64 and findings_digest. Findings
are canonical base64 of 1..1048576 exact UTF8 bytes with matching raw SHA256.
The immutable selected template admits outcomes; request IDs/digests grant nothing.

The retained writer rechecks actual CURRENT session, recipient/grant, source and
independence after waits/in the writer before new admission or exact replay.
New stage and exact same actor/key/command/original-session replay return200
with the same event/digest and actual Boolean replayed; signed is always false.
Conflict409 never outranks revoked/currentness refusal. No receipt/join/queue or
launch is created by the transport adapter. The finite error envelope is
`detail:{code}`, with only schema-required409 permitting bounded owner/action
guidance. Selected request absence/invisibility is404; explicit stage conflict,
stale source/authority, disabled runs and schema-required are409. Other
profile/authority failures map workflow_review_stage_authority_unavailable409.
Malformed stage/scope/outcome maps workflow_review_stage_invalid_request422;
database failures map workflow_review_stage_storage_unavailable500; retained
closure/template/activation or unexpected failures map
workflow_review_stage_storage_corrupt500, never caller422 or private details.

CLI invalid file/syntax/token/command exits2 before HTTP, success exits0 and prints
only the five acknowledgement fields under --json; refusal/transport/invalid ack
exits1. Plain output is `request_id: staged signed=false replayed=<boolean>
stage_event_id=<id>`. It posts once, closes its loopback client and preserves the
original key/body on uncertainty. Served OpenAPI includes both command and ack
families, closed session proof and finite errors; summary pins parameters/statuses
only. Browser classification excludes this POST; no browser adapter is provided.
Native denial decodes once, case-insensitive octets with UTF8 replacement:
%73tages and %2F separators can match; %FF in an org prefix is replaced before
prefix stripping. %2573 remains %73, decoded %3F is never a new query split,
query data is never decoded and '+' or malformed triplets stay literal.
Nonmatching targets use the existing policy. Human POST, selected GETs and
existing author-handoff denial retain their prior rules.

## THR299 reconciliation and nonmutating retry reads

Use the [closed server contract](orchestrator-contracts.md#exact-failed-continuation-reconciliation-thr299).
There is no browser write workflow. TypeScript `verifyRetryLink` mirrors the
three read modes; the POST is classified agent-only, with matching command and
receipt types. SurfaceAllowList mirrors the POST exclusion using the existing
task suffix deny convention after shipping path normalization, on prefixed and
unprefixed forms, before credential lookup/injection or daemon forwarding.
The advisory GET and adjacent browser task routes remain allowed. Shared
transport/auth and the path normalizer are unchanged.

```bash
happyranch verify-retry-link TASK-10565 --org happyranch --mode retry --target-agent dev_agent --failed-child-id TASK-10450 --json
happyranch verify-retry-link TASK-10565 --org happyranch --mode retry --target-agent qa_engineer --failed-child-id TASK-10329 --json
happyranch verify-retry-link TASK-10343 --org happyranch --mode preflight --version 1 --operation-key thr299-task10725-reconcile-v1 --existing-successor-id TASK-10554 --current-descendant-id TASK-10565 --thread-id THR-293 --operator-task-id TASK-10725 --session-id CURRENT_ORDINARY_TASK10725_SID --json
happyranch reconcile-failed-continuation TASK-10343 --org happyranch --from-file /absolute/command.json
happyranch verify-retry-link TASK-10343 --org happyranch --mode receipt --operation-key thr299-task10725-reconcile-v1 --json
happyranch audit --org happyranch TASK-10343 --action failed_continuation_reconciled --all-pages --json
```

Only the actual ordinary TASK10725 operator may preflight/write after reviewed
installation and serving-source verification. CURRENT_ORDINARY_TASK10725_SID is
its delivered transport SID, never another worker's session. Copy the actual
preflight expected_state_digest into the closed JSON with version1/key/existing
successor/current descendant/thread/operator/session; placeholders are not proof.
SID goes only in the preflight header or POST body, never the read URL. A stale
fresh digest refuses; do not auto-retry. Preserve exact original semantic bytes
and key on response loss and query receipt before deciding whether SAME-owner
POST replay is appropriate. Actual terminal owner loss blocks POST, while
historical receipt GET remains available. Genuine ordinary SAME-task resumption
may replay with its new SID without rewriting original evidence.

Both commands print JSON. Retry exits0 valid/1 invalid; write/read success0,
semantic/refusal/transport/storage/server error1, argument or local JSON-shape
error2. Write file paths must be absolute. Duplicate/unknown fields and mode
mixing refuse. The minimal receipt plus receipt_sha256 matches POST; historical
lookup never establishes current retry validity or authority to spawn a child.


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


## Disabled coding release catalog — K0

`happyranch workflows release-template coding --version 1 --org ORG --json`
uses the existing bearer client to GET
`/api/v1/orgs/{slug}/workflows/release-templates/coding/1`. Org selection retains
the existing CLI rules; no session or stage supplement is consumed. The CLI
validates the closed receipt and exact canonical decoded bytes before output;
parser errors exit2, transport/server/receipt errors exit1, valid catalog output
exit0. This is catalog success only, always `enabled:false` with
`reason:release_catalog_only`. The served `workflow-release-template@1` has only
format, identity, version, definition_bytes_base64, definition_digest,
compiler_pin, validator_pin, source_pin and readiness. The ordinary org GET is
classified included for unchanged native handling; no TS/provider/web initiation
caller is shipped in K0. Authentication and existing-org resolution precede the
package read. Unknown kind/version is404;503 resource_unavailable and500
resource_corrupt expose only category, not a resource path.

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

Sole ROOT TASK10693, manager step45 and Founder THR139seq562/559/329
bound `runtime/workflows/coding_saved_records.py` to pure, unmounted saved-record
inspection. Setup issue/withdraw commands preserve explicit null withdrawal;
mapping values/commands preserve original bytes, all selectors and generations,
fixed shipped K0 definition digest, four distinct agent roles, bounded eligible
sets and contextual underscore RoleKeys. Coding repository capsules retain K1's
org/key/origin identity, explicit owner membership and adoption pairing without
widening the document/K1 role grammar. Parsing grants no adoption or authority.

Mapping-record/setup/capability/issuance envelope readers check the specified
fields and equalities but **refuse** dependent captures with
`coding_saved_authority_serialization_unavailable`. The one missing contract
delta is the closed coding serialization of B2 `profile_evidence` and D1's
issuance `issuer`/`admission`: existing native capture types, admission rows and
Founder provenance do not specify those persisted nested coding values. No opaque
authority dictionaries, guessed serialization, resolver or trusted flag are
accepted. These are partial source readers, not complete K2a2a acceptance or
populated J compatibility. Full original command bytes/digests are retained;
there is no second reduced persisted form or digest-only replay.

All full relational/native↔J/event/history readers remain required before any
producer: native admission, source/result claim and task/session ancestry;
setup pointer/withdrawal and parent/action/radius attenuation; mapping generation
continuity and authentic adoption; run/context/binding/activation/task/request/
receipt/contributor closure; journal replay/CAS, host/cleanup/provider/effect
ownership; current selected access and all archived/current consumers. These
source grammars establish no authentic issuer, active grant or valid graph.
There is no store/roster/object/provider/network I/O. Empty paths/commands or
null provider/SID grant no corresponding current-use authority.

K2a1 continues rejecting every populated/reserved coding row, including with
`validate_data=False`; complete F–I/J literals/references/cache/migration/recovery
source is unchanged. K0 stays disabled, K1 current resolution unavailable,
create-org I and startup validate-only. No producer, mounting, new transport,
authority issuance, allocation, execution, migration or rollout is enabled.
Python tests are RETIRED; integration including callback SKIPPED24342; all
behavioral/security/negative/mutation/repetition/protected/executable proof is
UNEXECUTED/FOUNDER-WAIVED139429/430. AST/raw-byte/static inspection and required
lint/static checks supply no behavioral or test-authoring PASS. Independent FULL
review followed by same-final-head scoped SOURCE QA remain required.
