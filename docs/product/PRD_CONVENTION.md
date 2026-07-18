# Lightweight PRD Convention

Source: THR-108 message 3.

HappyRanch uses lightweight PRDs for product-shaped changes where ambiguity is expensive: new founder-facing workflows, changed agent/runtime behavior, permission or safety model changes, paid or commercial features, and cross-team capability changes.

Do not require PRDs for bug fixes, internal refactors, mechanical UI polish, or implementation plans where the product decision is already settled. Do not backfill older docs into this format unless the Founder asks for a specific old decision to be reopened.

## Location

New PRDs live in:

```text
docs/product/prds/
```

Use a dated, descriptive filename:

```text
YYYY-MM-DD-short-feature-name.md
```

## Statuses

- `draft`: Product is still framing the decision.
- `founder-ruled`: Founder has resolved the key product decisions.
- `build-spec`: Ready for Engineering Manager build planning.
- `shipped`: Implemented and accepted.
- `superseded`: Replaced by a newer PRD or founder ruling.

## Ownership

Product Lead owns the PRD draft, problem framing, goals, no-list, and acceptance signal. Founder owns final product rulings and any roadmap or external timeline commitment. Engineering Manager owns the build plan against a locked PRD. QA and product acceptance verify against the PRD acceptance criteria.

## Commitment Boundary

Every PRD must state its commitment boundary in the header:

- `analysis-only`: Frames the option space; does not authorize build work.
- `build-ready`: Product scope is locked enough for implementation planning.
- `roadmap commitment`: Founder-approved commitment to deliver; must not be implied by Product alone.

Do not put external timeline commitments in a PRD unless the Founder has explicitly approved them.

## Template

```markdown
# <PRD Title>

| Field | Value |
| --- | --- |
| Status | draft / founder-ruled / build-spec / shipped / superseded |
| Owner | <name or role> |
| Date | YYYY-MM-DD |
| Source Links | <thread/task/design/doc links> |
| Commitment Boundary | analysis-only / build-ready / roadmap commitment |
| Founder Decisions | Required: <list>; Ruled: <list or none> |

## Problem

What user/customer/business problem are we solving? What breaks, slows down, or becomes risky if we do nothing?

## Users And Workflow

Who is the target user, and what is the core workflow before, during, and after this change?

## Goals

- <Goal 1>
- <Goal 2>

## Non-Goals

- <Explicitly out-of-scope item>

## No-List

- <Tempting idea we are deliberately not doing>

## Success Signal

What measurable metric, user-visible acceptance signal, or operational outcome tells us this worked?

## Phase Scope

### v1 Cutline

What must ship in v1 for the change to be useful?

### Later

What is intentionally deferred?

## Functional Requirements

- <Requirement engineering can implement and QA can verify>

## Data And Provenance Requirements

What data is read, written, derived, or displayed? What source of truth backs each important user-visible claim? For HappyRanch runtime surfaces, call out where the UI must render stored facts rather than inferred status.

## Acceptance Criteria

- <Observable pass/fail criterion>

## Open Questions And Risks

- <Question or risk>
```

## Length

Aim for 2-4 pages. If the PRD grows beyond that, split product decisions from engineering design. The PRD should make the product call clear; it should not absorb implementation architecture unless that architecture changes the product commitment.
