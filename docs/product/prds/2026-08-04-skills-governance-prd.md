# Skills catalog and governance PRD

| Field | Value |
| --- | --- |
| Status | shipped |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 seq. 18–19; THR-145 seq. 2–3; current catalog/lifecycle web and daemon contracts |
| Commitment Boundary | analysis-only — preserves the current lifecycle boundary; no authority or workflow expansion |
| Founder Decisions | Ruled: guidance-only skills, founder-authenticated review, and source-gated read-only cutline. Required: any participant/action/authority expansion. |

## Problem and outcome

The founder needs reusable agent guidance governed without confusing it with permissions, and needs evidence that distinguishes authored/versioned content, technical validation, human decision, assignment, and actual materialization. The outcome is a truthful ledger and bounded web cutline—not a generic package-status machine.

## Users and authority baseline

Founder-authenticated review is server-gated; web controls are not proof of authorization. Verified active agent sessions may submit only through the current narrow agent-id × canonical-skill pilot and cannot view or mutate human review. System-contract and platform-managed skills remain read-only. A skill never grants tools, commands, credentials, or permissions.

## Shipped constraints

- Immutable proposal/decision history is separate from assignment and materialization/effectiveness projections. Rejection is terminal; rollback/unassignment never rewrites package decision history.
- A saved custom draft remains after validation failure with failure evidence. Validation is technical, not approval.
- `assigned_not_yet_effective` is distinct from effective when materialization version does not match. An earlier effective version may remain during failed new materialization.
- The current web has catalog/detail/create/edit, validation, bounded assignment/status and founder-only proposal queue/detail actions: claim, validate, submit review, approve, reject. Proposal detail intentionally excludes publish, assign, rollback, and stale-refresh mutations.

## Scope and non-goals

In scope: catalog, custom draft/edit validation, technical evidence, status/effectiveness projections, bounded assignment, source gates, proposal queue/detail and its existing actions.

Non-goals: in-console import/review editing beyond routes; generalized package statuses; publish/assign/rollback controls in proposal detail; agent access to human review; custom modification of system/platform skills; permissions; expanded pilot participants; and any client-side role gate.

## Functional requirements

1. **FR-1–3:** describe and enforce guidance-only semantics, source class, stable identifier, version, and source-gated editability.
2. **FR-4–5:** preserve custom draft plus concrete failure reason on validation failure, and present validation as technical evidence only.
3. **FR-6–7:** show assignment, materialization/effectiveness, latest version, and `assigned_not_yet_effective` as distinct projections.
4. **FR-8–9:** permit bounded assignment only for eligible validated user-authored content; render Runtime Validation/lifecycle status as read-only evidence.
5. **FR-10–13:** expose the current founder-only proposal actions and immutable proposal/decision evidence; enforce narrow agent submission and withhold human-workflow data from other sessions.

## Workflow and state behavior

Custom author saves draft → validates → receives retained pass/fail evidence → eligible content is assigned → materialization produces effective/version-mismatch projection. Separately, a verified pilot agent submits immutable proposal → founder may claim/validate/submit review/approve or reject → terminal rejection/history is retained. Assignment/effectiveness and publish/rollback are not inferred as proposal-package status transitions.

## API and data dependencies

Catalog/detail/create/edit/validate/status/assignment APIs; validation event ledger; materialization/effectiveness projections; lifecycle proposal queue/detail/action routes; verified session/pilot policy; source class/policy fields. Data must preserve version, actor/action/rationale where retained, validation evidence, assignment scope, and materialized version.

## UX and accessibility criteria

Show state dimensions separately with text and semantics, not a single color/status badge. Clearly label read-only, source-gated, technical-validation, human-review, terminal rejection, and not-yet-effective states. Forms retain user input and bind errors to fields; queue/detail actions are keyboard reachable with explicit confirmation/outcome and truthful 401/403 feedback.

## Acceptance criteria

- A failed custom validation leaves an editable saved draft and concrete error evidence.
- System-contract/platform-managed content cannot be edited or unassigned via web; guidance copy never claims permission grants.
- Assignment before matching materialization reads `assigned_not_yet_effective`, and rejected proposal history remains visible/immutable.
- Proposal detail exposes only claimed bounded actions; publish/assign/rollback and human-review access are unavailable without their canonical route/authority.
- All lifecycle states have accessible loading/empty/error/populated presentation and server denials are not disguised as frontend role gates.

## Metrics

Track validation pass/failure/reason categories, time draft→valid→effective, assigned-not-effective duration, proposal action outcomes, terminal rejections, materialization mismatch/failure rate, and unauthorized/withheld accesses. These are governance evidence, not performance promises.

## Risks and gates

Risk: collapsing ledger, assignment, and runtime projection into one status misstates authority/history. Mitigation: separately model and label dimensions. Risk: prototype controls imply mutation authority.

**Engineering gate:** schema/route review before lifecycle state/action or materialization-record expansion. **Founder gate:** participant expansion, publish/assign/rollback UI, delegated review, or any permission semantics. No authorization is granted by this PRD.
