# Prototype mutation families — decision-gated planning package

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-05 |
| Source Links | THR-140 seq. 86–101; updated `screens.zip` (`THR-140-20260805T054438Z-screens.zip`); PR #555; current daemon/web contracts; TASK-4410 reconciliation |
| Commitment Boundary | analysis-only — no schema, API, authority, retention, or UI mutation is authorized |
| Founder Decisions | **Pending:** D1–D6 in the ledger below. Separately ruled: #576 makes Settings → Organization the sole editable owner for Work Hours enablement and eligibility; #577 is a navigation-only AppShell grouping pilot after #576. Founder-authenticated actions are the present baseline. |

## Recommendation

Keep all five prototype mutation families out of #576 and #577. Open them only as separately ratified, auditable contracts, beginning with one narrow family at a time. The safe common rule is: a prototype button is not a permission, and a successful technical check is not a human governance decision.

The first decision is not which screen to build. It is whether the Founder wants to authorize a **founder-only, server-enforced mutation pilot** for any family. Until that decision, the UI must either link to an existing canonical action or show the capability as unavailable with its reason.

## Evidence boundary and factual baseline

This plan reconciles three kinds of evidence without treating them as equivalent:

- The updated `screens.zip` is visual/workflow intent. Its Skills edit/import/assign, candidate, assistant approval, retention/export/history, and Job-context controls show potential outcomes, not approved contracts.
- PR #555 and THR-140 establish the current product boundary: #576 changes the placement of an existing Work Hours owner; #577 changes AppShell grouping. Neither creates new authority, routes, record types, or retention policy.
- Current implementation establishes the factual baseline. B2 custom Skills use one immediately editable record for verified agents and founders, default Hidden — eligibility not configured, while retaining version, provenance, validation, and materialization evidence; Dream KB candidates have limited persistence in the daemon but no established **general browser candidate-governance contract**; audit is append-only; artifact provenance is limited; the assistant has conversation/tool evidence but no governance-write route; and `JobRecord` lacks prototype `thread`, `routed_via`, `kind`, and `pr_ref` fields.

The browser has no generalized role model. Server-side authorization, organization scope, and participant checks are authoritative; founder-authenticated actions are the current baseline. This package does not approve delegation, remote operators, client-side RBAC, autonomous approval, or a new generic “operator” role.

## Shared mutation contract — prerequisite, not an implementation request

Every future family-specific write must use its canonical record rather than a dashboard, prototype panel, or assistant transcript as the source of truth. A proposed write contract must include:

1. A named canonical record, immutable record ID, organization scope, current version/revision, and lifecycle state.
2. A server-enforced actor and action allowlist. In the first pilot the actor is the founder; an assistant or agent may propose but cannot become the approving actor.
3. An append-only decision/audit event containing actor, action, rationale, timestamp, request/correlation ID, before/after revision or content hash, and linked source records.
4. Explicit preview → confirmation → execution → receipt behavior for consequential writes. Confirmation binds the exact target/version; it is not a generic “are you sure?” modal.
5. Idempotency keys for retryable requests; `409`/precondition responses for stale state; a readable refusal for `401`/`403`; and no hidden client-side success.
6. Loading, empty, unavailable, denied, validation-failed, conflict, partial-failure, and refreshed-success states. A retry must never duplicate a decision, promotion, export, or action.
7. Data minimization, redaction, and retention classification before copying task, conversation, job, or artifact contents into a new record.

This common shape intentionally does **not** create a generalized authorization service. Each family must still name its own actor, target, lifecycle, and founder ruling.

## Pending decision ledger

| ID | Founder decision needed | Recommendation | What is deliberately given up if accepted |
| --- | --- | --- | --- |
| D1 | Whether to authorize any founder-only mutation pilot now, and which one family is first | Authorize no more than one family after its contract review; start with a bounded candidate-review or Job-context read model, not assistant writes or retention controls | A broad “make all prototype buttons work” release |
| D2 | Whether custom Skills need a new capability beyond the completed B2 authoring and eligibility model | Keep B2’s immediately editable record, default-hidden eligibility, immutable versions, provenance, validation, and materialization evidence; require a separately scoped ruling for any new capability | Unbounded catalog mutation, delegated rollout, or permission changes |
| D3 | Which Knowledge/Dream candidate sources and terminal actions are allowed, and whether acceptance may create/update shared KB | Keep candidate review separate from KB mutation; Founder decides candidate classes and explicit promotion semantics | One-click auto-promotion and untraceable direct KB edits |
| D4 | Whether the assistant may execute any governance write, and the exact target/action allowlist | Keep assistant navigation-only unless one canonical action family, confirmation model, and audit receipt are approved | Conversational approve/reject across arbitrary records |
| D5 | Retention, deletion, export, and review-history policy for operational evidence | Do not add generic retention/delete/export/history controls before a written policy, data classification, and export audit contract | A polished evidence-control UI in the near term |
| D6 | Whether to persist JobRecord context/action data and which fields are authoritative | Add immutable, optional submission context only after Engineering proposes a backward-compatible model; preserve current job review actions | Prototype Job rail fields, inferred PR links, and a generic “action context” blob |

## Family 1 — B2 custom-skill actions

### Customer outcome and current factual baseline

The customer outcome is reusable internal guidance whose origin, target audience, and effective version can be reconstructed without ever turning a skill into a permission grant. B2 uses the same immediately editable custom-skill record for verified agents and founders. It is default Hidden — eligibility not configured, and retains immutable versions, provenance, deterministic validation, and runtime materialization evidence.

Legacy proposal UI, content, records, history, routes, adapters, and compatibility behavior are deleted. System-contract and platform-managed skills remain outside custom-skill mutation.

### Current B2 workflow and boundaries

1. A verified agent creates a task/session-bound custom skill directly, or a founder creates the same record in the web console.
2. The service validates the exact content and retains an immutable version, provenance, and validation evidence.
3. The record remains editable but hidden until the founder configures explicit organization, team, or agent eligibility.
4. Eligibility affects only future-session guidance visibility; it never grants tools, credentials, sandbox access, or authority.
5. Materialization is recorded separately from validation and eligibility.

No skill field may change sandbox, tools, credentials, allow rules, or agent/repository configuration. Any capability beyond this B2 model requires a separately scoped ruling.

## Family 2 — Knowledge and Dreams candidate actions

### Customer outcome and current factual baseline

The customer outcome is converting a potentially reusable observation into shared knowledge deliberately, while preserving its dream/task origin and keeping private learning separate from the organization KB. The daemon has a limited `DreamKbCandidate` persistence shape (`pending`, `promoted`, `rejected`, `superseded`) and accept/dismiss implementation, but the current web surfaces are evidence/triage and do not establish a general, reviewed browser contract for the prototype's approve/reject/promote/import/edit actions. The limited record captures content, source dream/agent, rationale, and promoted KB slug; it does not establish actor, decision-reason, revision, conflict, or retention semantics for the wider action set. KB create/update/delete/reindex are separate current routes.

### Proposed target workflow and canonical lifecycle

If D3 is approved, use one candidate record for candidate-origin actions, with `source_type` (`dream`, task proposal, human intake), immutable source snapshot/hash, sensitivity label, and target KB location. The workflow is:

1. Generate or submit candidate → `pending_review`; it does not alter shared KB.
2. Founder opens the source evidence and chooses `dismissed`, `needs_revision`, or `approved_for_promotion` with rationale.
3. Promotion creates a linked KB revision through the canonical KB write, records the resulting revision/slug, then marks the candidate `promoted`.
4. A KB edit conflict, duplicate slug, policy refusal, or failed write leaves the decision/event visible as `promotion_failed` or `conflicted`; it never silently becomes promoted.
5. A newer candidate or newer KB revision may mark an older candidate `superseded` without deleting its decision evidence.

`KnowledgeCandidate`, `CandidateRevision`, `CandidateDecision`, and `KbPromotionLink` are the canonical records. Existing dream candidate records may migrate only through an explicit Engineering mapping; do not infer missing actor/rationale/history from current rows.

### Authority, API/data/audit, privacy, and conflict needs

First-pilot authority is founder-only: generating a candidate is not shared-KB authority; accepting is not an implicit unrestricted KB editor. The server must check organization, candidate state/version, source access, and target-KB conflict. Audit stores source type/ID, snapshot hash, reviewer, decision/rationale, target KB slug/revision, request ID, and failure/conflict result.

Candidate body and dream/task context may contain sensitive internal facts. The plan requires source-aware redaction, restricted preview/download treatment, content-size limits, and a decision on whether candidate contents share task/artifact retention or a dedicated policy. API needs explicit preview, decide, and promote operations; promotion is idempotent and conditional on the accepted candidate revision and target KB revision.

### Visual design, dependencies, non-goals, acceptance

Design needs a source-evidence drawer, sensitivity/freshness badge, candidate revision view, decision rationale field, exact target preview, and a post-action receipt linking the created KB revision. It must visibly distinguish “accepted for promotion” from “promoted.” Empty, unavailable-source, duplicate, stale, denied, failed-promotion, and already-decided states are required.

Dependencies: authoritative current Dream candidate behavior; KB revision/conflict semantics; retention/redaction policy (D5 where content is retained/exported). Non-goals: automatic promotion, silent overwrite of KB entries, agent-authored shared KB writes, bulk triage, candidate scoring as authority, or candidate actions in #576/#577.

Acceptance: a pending candidate does not alter KB; the same decision request is idempotent; a competing decision returns a traceable conflict; a promotion creates exactly one linked KB revision or a retained failure record; the audit reconstructs source→decision→KB revision; and no candidate controls claim capability when its source, policy, or authority is unavailable.

## Family 3 — Assistant governance writes

### Customer outcome and current factual baseline

The outcome is a faster founder path from assistant analysis to a decision while retaining a record that makes clear who decided and what changed. The current assistant provides conversation management, streamed/tool evidence, and navigation/handoffs. It has no approved browser governance-write authority. The prototype's approve/reject affordances are therefore not a safe current workflow; the assistant is not a decision-maker.

### Proposed target workflow and canonical lifecycle

If D4 is approved, begin with one named canonical action family only; do not create a generic natural-language write API. The assistant identifies a target and produces a structured proposal: target type/ID, expected revision, proposed canonical action, effect summary, evidence links, and reason. The founder opens a canonical preview, confirms the exact request, and the canonical service executes the existing or newly approved action. The result is an immutable `AssistantActionReceipt` linked to the conversation message, proposal snapshot, actor, canonical event, and outcome.

Lifecycle: `suggested → previewed → confirmation_pending → submitted → applied | refused | conflicted | failed | cancelled`. The assistant may generate `suggested`; only the founder may initiate confirmation; only the canonical service applies a write. A navigation handoff remains the default for all other requests.

### Authority, API/data/audit, privacy, and conflict needs

D4 must specify the exact target/action allowlist (for example, one existing job-review action **or** one existing proposal-decision action—not both by default), whether a second confirmation is required, and the stop/rollback behavior. The assistant cannot expand the allowlist from prompt text, can never self-confirm, and must be server-denied for unlisted or stale actions.

The API contract needs a typed target schema, expected revision, idempotency key, explicit confirmation nonce, canonical authorization check, and receipt endpoint. Audit must join assistant conversation/message/tool evidence, founder identity, target/revision, requested and executed action, reason, timestamp, request ID, and canonical action event. Conversation/tool contents need minimization/redaction; privileged source content must not be copied into receipts unnecessarily.

### Visual design, dependencies, non-goals, acceptance

Design needs a clearly labeled recommendation card—not an action button—plus preview, impact/evidence, authority, confirmation, receipt, and “open canonical record” states. Because the assistant dock is already an announced dialog, any future confirmation must use an accessible non-nested interaction pattern. The UI must show disconnect, expired confirmation, stale target, refusal, retry-safe failure, and success-after-refresh. Tool activity/citations remain evidence, never proof of execution.

Dependencies: D4, an approved canonical target contract, assistant session security, audit correlation, and design of confirmation/recovery. Non-goals: autonomous decisions, free-form multi-record writes, background approval, delegated role routing, assistant-originated policy changes, or a conversational replacement for canonical record pages.

Acceptance: unlisted targets are denied server-side; no proposed action changes state before founder confirmation; repeating a confirmation does not duplicate the canonical action; a changed target revision produces a recoverable conflict; every applied action has a linked receipt and canonical audit event; and cancelling/disconnecting leaves no ambiguous applied state.

## Family 4 — Evidence retention, export, and review-history controls

### Customer outcome and current factual baseline

The customer outcome is being able to find, review, preserve, share, and eventually dispose of operational evidence without claiming records exist when they do not. Current artifact views expose stored metadata/provenance; filename and `modified_at` are the only safe derived provenance for certain artifacts. Audit is append-only. The updated prototype itself says checks, file diffs, and review history are not tracked in v1. Existing client-side exports of a currently rendered view do not establish a governed evidence export or retention policy.

### Proposed target workflow and canonical lifecycle

If D5 is approved, define evidence as a governed record family before styling controls. Canonical records are `EvidenceObject` (content/metadata pointer plus classification), immutable `EvidenceRevision`/hash, `ReviewEvent`, `RetentionRule`, `LegalOrIncidentHold` if applicable, `ExportRequest`, and `DeletionEvent`/tombstone. The lifecycle is `captured → retained → reviewed* → export_requested → export_ready/failed/expired → retention_due → deleted_or_tombstoned`, where `reviewed` is an event stream rather than a mutable status.

No control may claim a file diff, check result, review, retention countdown, or deletion when no matching persisted record exists. A delete action must be defined as a recoverable logical deletion, irreversible purge, or prohibited action by policy; this plan does not choose among them.

### Authority, API/data/audit, privacy, and conflict needs

D5 must name retention classes/durations, default treatment of task/thread/assistant/job artifacts, sensitive-content/redaction rules, export recipients/destinations, maximum export scope, handling of holds/incidents, and deletion semantics. First-pilot authority should remain founder-only; thread attachments retain their existing participant-scoped access and must not become organization-wide exports by accident.

APIs require content/revision identity, policy/version applied, source scope, requestor/approver, export manifest and checksum, expiry/download count if applicable, immutable access/denial events, and conflict checks against retention holds or changed revisions. Export must be generated server-side from a declared snapshot, with redaction before delivery and no implicit live filesystem access. Audit correction remains a new event, never a historical rewrite.

### Visual design, dependencies, non-goals, acceptance

Design needs a factual evidence rail (source, captured time, revision/hash if present, classification, policy), an explicit unavailable state for non-persisted review/diff/check data, scoped export wizard, confirmation of irreversible operations, manifest/receipt, and audit timeline. Export is asynchronous, not a blocking spinner. It must show policy/hold denial, stale export snapshot, redaction notice, failed/expired export, empty history, and permitted download without revealing protected content.

Dependencies: D5 policy/risk review, content classification and redaction implementation, artifact/attachment access model, storage lifecycle capability, and Engineering feasibility. Non-goals: arbitrary file-system browsing, blanket org data dumps, public links, inferred review history, default hard delete, legal/compliance claims, and work in #576/#577.

Acceptance: every visible history/check/diff value has a persisted source record; an export manifest exactly describes the snapshot and redaction policy applied; unauthorized/participant-ineligible access is denied; a hold blocks deletion and leaves an audit event; retrying export does not create ambiguous duplicate deliveries; and retention/deletion views never fabricate dates or policy.

## Family 5 — JobRecord context and action data

### Customer outcome and current factual baseline

The customer outcome is deciding whether to run, reject, stop, or investigate a job with enough trustworthy context to understand its origin and effect. Current `JobRecord` contains task linkage, agent, title/rationale, command/interpreter, review requirement, runtime/output and review/stop result fields. Current job mutations are canonical job actions. It does **not** contain prototype `thread_id`, `routed_via`, `kind`, or `pr_ref` fields; the only reliable current backlink is the task. The prototype's approval-context rail truthfully labels these as requiring new fields.

### Proposed target workflow and canonical lifecycle

If D6 is approved, add an immutable, optional `JobSubmissionContext` recorded at job creation rather than retrofitting inferred facts into old jobs. It contains typed source references: `origin_task_id` (required), optional `origin_thread_id`, optional routing event/reference, a controlled `job_kind`, and structured external-reference entries such as `pr_ref` only when supplied by an authoritative integration. The actual job lifecycle remains canonical: `pending_review → approved/running → completed/failed/stopped/rejected`; context is not a second action state machine.

For action data, every run/reject/stop event must reference the job revision and submitted context snapshot, actor, rationale, and outcome. If a future action needs extra approval evidence, create a typed `JobReviewDecision` event rather than an opaque context blob.

### Authority, API/data/audit, privacy, and conflict needs

The current founder-authenticated job action model remains baseline. D6 must decide which context fields are worth persisting, whether external PR references are merely display links or verified integrations, whether context is immutable after submission, and whether existing jobs remain blank/unavailable rather than backfilled. Engineering must choose a backward-compatible schema/API: optional nested context or linked table, explicit versioning, safe migrations, and list/detail response compatibility.

API requests need provenance validation (referenced task/thread/routing event belongs to the same organization), type validation for `job_kind`, expected job state/revision for run/reject/stop, idempotency, and output/error redaction. Audit preserves submitter, reviewer, reason, source refs, command hash where policy permits, status transition, and correlation ID. Do not persist tokens, secrets, raw prompt bodies, or unbounded assistant transcripts as “context.”

### Visual design, dependencies, non-goals, acceptance

Design needs an approval-context rail that renders only present fields, marks legacy records “not captured,” links validated source records, distinguishes an external reference from verified PR status, and keeps run/reject/stop on the canonical job action surface. Pending-review, reviewed, running, terminal, denied, stale, output-unavailable, and legacy-record states must be explicit. Context must not make a job look safe or approved.

Dependencies: D6, Engineering schema/API feasibility and migration plan, task/thread linkage consistency, external-reference verification policy, and privacy/redaction review. Non-goals: free-form execution from the context rail, creation of new job types by UI string, inferred PR/thread/routing values, retroactive history fabrication, or assistant direct job approval without D4.

Acceptance: a newly submitted job preserves valid typed context across list/detail/refresh; old jobs show unavailable context rather than invented values; an invalid cross-org reference is denied; a stale review action conflicts safely; job action audit reconstructs the exact source-context snapshot; and command/output access follows existing redaction/authorization rules.

## Delivery sequence and gates

| Sequence | Deliverable | Gate to advance | Explicitly not authorized by this plan |
| --- | --- | --- | --- |
| 0 | Skills legacy-surface reconciliation note | Engineering confirms all 410-backed catalog entry points are retired/redirected; this is a separate defect fix, not a new mutation capability | Restoring a parallel catalog API or changing lifecycle authority |
| 1 | Founder resolves D1–D6 and selects one family | Written Founder ruling identifies first family, actor, and cutline | Any code, schema, UI mutation, delegation, or pilot launch |
| 2 | Engineering contract note for selected family | Canonical record, lifecycle, API/data migration, failure/conflict, privacy, and audit review accepted | Cross-family framework or generic roles service |
| 3 | Product build-spec + visual-state handoff | Founder approves the selected contract and states authority/retention boundary | Design-led #576/#577 scope expansion |
| 4 | Small founder-only implementation pilot | Automated authorization/conflict/audit proofs plus a founder-observed end-to-end record reconstruction | Broad rollout, agent self-service, delegated operators |
| 5 | Pilot evidence review | Founder reviews adoption, refusals, conflicts, privacy incidents, recovery and audit completeness | Second family or expanded action set without a new ruling |

Recommended order: (a) repair/retire the 410-backed Skills legacy UI as a bounded reconciliation item; (b) agree the common decision-record/audit pattern before any Knowledge/Dream candidate contract; (c) choose one low-blast-radius founder pilot—candidate review or JobRecord read-only context; (d) expand Skills only after lifecycle-cutover and data-model proof; (e) decide evidence policy before retention/export; and (f) consider assistant writes last, for one existing canonical action family only. This is a recommendation, not a roadmap commitment.

## Cross-family measurable success and no-list

For any approved pilot, success requires: 100% of state-changing calls are server-authorized and carry an immutable audit/correlation record; tests cover allowed, denied, stale/conflict, retry, failure, cancellation, and refresh paths; a founder can reconstruct one outcome from source evidence to final state; and the UI never shows an unavailable capability as a usable action. Product telemetry should measure action attempts/outcomes, server denials, stale conflicts, retries, time to decision, audit reconstruction success, redaction/export failures where relevant, and rollback/recovery events—not vanity button clicks.

Across all families, do not build a generic operator/RBAC model, grant agents approval rights, infer provenance, silently overwrite records, retrofit missing history as fact, treat assistant text as authority, expose secrets in context/export, use a client-side role check as enforcement, or fold any mutation into the #576/#577 pilots.
