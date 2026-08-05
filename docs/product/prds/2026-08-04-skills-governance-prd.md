# Skills catalog and governance PRD

| Field | Value |
| --- | --- |
| Status | shipped |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 seq. 18–19; THR-145 seq. 2–3; current daemon legacy-cutover and lifecycle routes; current web API/provider and route inventory |
| Commitment Boundary | analysis-only — preserves the current lifecycle boundary; no authority or workflow expansion |
| Founder Decisions | Ruled: guidance-only skills, founder-authenticated review, and source-gated read-only cutline. Required: any participant/action/authority expansion. |

## Problem and outcome

The founder needs reusable agent guidance governed without confusing it with permissions, and needs evidence that distinguishes authored/versioned content, technical validation, human decision, assignment, and actual materialization. The outcome is a truthful lifecycle ledger with a bounded, read-oriented catalog—not a generic package-status machine or a second direct-write workflow.

## Users and authority baseline

Founder-authenticated review is server-gated; web controls are not proof of authorization. Verified active agent sessions may submit only through the current narrow agent-id × canonical-skill pilot and cannot view or mutate human review. Founder lifecycle transitions are separately bearer-gated. System-contract and platform-managed skills remain read-only. A skill never grants tools, commands, credentials, or permissions.

## Shipped constraints

- Immutable proposal/decision history is separate from assignment and materialization/effectiveness projections. Rejection is terminal; rollback/unassignment never rewrites package decision history.
- The direct catalog mutation endpoints—`POST /skills`, `PATCH /skills/{skill_id}`, `POST /skills/{skill_id}/validate`, and `POST /agents/{agent_id}/skills/{skill_id}/assign`—are retired compatibility endpoints. Each returns `410 Gone` with `code: legacy_cutover`; legacy user-authored entries are quarantined/read-only rather than an alternative catalog write source.
- The lifecycle proposal ledger is the sole current Skills write boundary: submission creates a versioned proposal; lifecycle validation, review decision, publication, assignment, rollback, and retirement occur only through `/skill-lifecycle` routes under their server authority gates. Technical validation remains evidence, not approval.
- `assigned_not_yet_effective` is distinct from effective when materialization version does not match. An earlier effective version may remain during failed new materialization.
- The still-mounted legacy catalog controls—including `/skills/new`, `/skills/:skillId/edit`, revalidation, and assignment affordances—are retired compatibility UI, not a working acceptance flow. Some provider adapters currently translate legacy-named calls to lifecycle requests; that implementation detail does not reinstate direct catalog authoring as product scope. The mounted routes are an implementation-reconciliation defect to triage separately, not an approved remediation or a product-build commitment.

## Scope and non-goals

In scope: lifecycle proposal/decision history, its server-gated write transitions, published-catalog and status read projections, technical evidence, assignment/materialization projections, source gates, and founder proposal queue/detail evidence.

Non-goals: reviving or accepting direct catalog create/edit/validate/assign flows; treating mounted legacy pages as a supported workflow; generalized package statuses; agent access to human review; custom modification of system/platform skills; permissions; expanded pilot participants; and any client-side role gate.

## Functional requirements

1. **FR-1–3:** describe and enforce guidance-only semantics, source class, stable identifier, version, and source-gated read access.
2. **FR-4–5:** reject each direct catalog mutation with `410 legacy_cutover`; accept every current Skills write only through the lifecycle proposal ledger and its authority checks.
3. **FR-6–7:** retain technical validation evidence and immutable proposal/decision history; validation does not itself approve, publish, assign, or materialize a version.
4. **FR-8–9:** show assignment, materialization/effectiveness, latest version, and `assigned_not_yet_effective` as distinct projections; an assignment change never rewrites a proposal or decision.
5. **FR-10–13:** enforce narrow verified-session submission, founder-only lifecycle transitions, and withholding of human-workflow data from other sessions. Treat the mounted legacy catalog pages as an implementation-reconciliation defect, not as acceptance scope.

## Workflow and state behavior

A verified pilot agent or founder-authenticated caller submits a new immutable proposal version → the lifecycle records technical validation evidence → the founder claims/reviews/decides under server gates → an approved version may be published and assigned → materialization produces an effectiveness/version-mismatch projection. Rejection is terminal and a changed package requires a new proposal version. Direct catalog save, edit, validate, and assign calls stop at `410 legacy_cutover`; they never create a draft, decision, assignment, or materialization record. Assignment/effectiveness and rollback/retirement remain projections/actions separate from proposal and decision history.

## API and data dependencies

Read compatibility: catalog/detail/status and Runtime Validation evidence. Retired direct writes: catalog create/edit/validate/assign, each `410 legacy_cutover`. Canonical writes: `/skill-lifecycle/proposals` (including the verified-session agent proposal path), lifecycle proposal review/action routes, and lifecycle publication/assignment/rollback/retirement routes. Data must preserve version, actor/action/rationale where retained, validation evidence, assignment scope, and materialized version.

## UX and accessibility criteria

Show state dimensions separately with text and semantics, not a single color/status badge. Clearly label read-only, source-gated, technical-validation, human-review, terminal rejection, and not-yet-effective states. Current proposal queue/detail views render authoritative evidence with accessible loading, empty, error, and founder-denial states. The mounted legacy catalog forms are not a product acceptance surface; their reconciliation must not be hidden by a client-side role gate or by a false success state.

## Acceptance criteria

- Each direct catalog create, edit, validate, and assign endpoint returns `410 Gone` and `legacy_cutover`; no response claims a direct catalog write succeeded.
- A proposal is the only current creation/change record. Its validation, decision, publication, and assignment transitions retain server-authoritative evidence and authority provenance.
- Assignment before matching materialization reads `assigned_not_yet_effective`, and rejected proposal history remains visible/immutable after rollback, retirement, or assignment changes.
- The crosswalk classifies `/skills/new` and `/skills/:skillId/edit`, plus the legacy catalog mutation controls, as an Engineering reconciliation defect—not working Skills acceptance and not product-authorized remediation.
- Lifecycle evidence views have accessible loading/empty/error/populated presentation; server denials are not disguised as frontend role gates.

## Metrics

Track proposal-validation pass/failure/reason categories, time proposal→validated→effective, assigned-not-effective duration, proposal action outcomes, terminal rejections, materialization mismatch/failure rate, and `legacy_cutover` attempts. These are governance evidence, not performance promises.

## Risks and gates

Risk: collapsing ledger, assignment, and runtime projection into one status misstates authority/history. Mitigation: separately model and label dimensions. Risk: mounted legacy UI is mistaken for an accepted workflow despite the direct API cutover.

**Engineering reconciliation defect:** decide the bounded technical remedy for mounted legacy catalog pages only through a separately scoped engineering change; this PRD does not authorize it. **Engineering gate:** schema/route review before lifecycle state/action or materialization-record expansion. **Founder gate:** participant/action/authority expansion, revival of direct catalog writes, delegated review, or any permission semantics. No authorization is granted by this PRD.
