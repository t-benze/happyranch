# THR-055 Skill Proposal Review — Server Contract & UI Slices

**Status:** Current (2026-08-03) — supersedes §4.5 lifecycle wording in protocol/05c-orchestrator.md for the review surface.

**Approved sources:** TASK-4045 design handoff, TASK-4098 (Slice 2A), TASK-4128 (Slice 2B), TASK-4154 (Slice 3A), protocol/05c-orchestrator.md §4.5.

**Scope:** Server contract + TypeScript API mirror + read-only founder UI.
Slice 1 (PR #546): founder-only proposal review routes + concurrency + state machine.
Slice 2A (PR #549): immutable SKILL.md bytes in detail, typed server filters in queue.
Slice 2B (PR #550): read-only Founder Proposal Detail page.
Slice 3A (PR #551): read-only Founder Proposal Queue page. **All action/confirmation/stale-refresh mutation UI remains deferred to later slices.**

---

## 1. Data Model Corrections (Additive)

These corrections are narrow additive repairs to the existing lifecycle model
in `runtime/skills/lifecycle/`. No existing columns are dropped, altered, or
semantically repurposed.

### 1.1 Immutable Author + Separate Claimant

- `PackageVersion.created_by` / `proposer_agent` — **immutable** proposer identity,
  derived from verified server-side session context. Never overwritten by any
  lifecycle action.
- `PackageVersion.claimed_by` / `claimed_at` — optional, separate founder claimant
  identity and timestamp. Additive nullable columns (`ALTER TABLE ADD COLUMN`).

### 1.2 Terminal REJECTED Semantics

- `LifecycleStatus.REJECTED = "rejected"` — terminal decision status.
- After rejection, every later claim, validation, review/approval, publish,
  assign, materialization, rollback/reopen/recovery attempt is blocked with
  error code `rejected_terminal` (HTTP 409).
- Rejection retains immutable package, all evidence, actor/time/rationale,
  and append-only history. A future change is a new proposal/version only.

### 1.3 Decision Lifecycle vs Assignment Projection

- Package decision lifecycle ends at `published` (or `rejected` terminal).
- Assignment/unassignment/materialization are append-only version-pinned
  projections — they do NOT set package status.
- `rollback` no longer mutates package status (was setting `ROLLED_BACK`).
- Historical `ROLLED_BACK`/`RETIRED` rows retain their status for
  compatibility; new flows never generate these as package status.

### 1.4 Reproducible Validation

- Validation events record: `content_hash` (immutable from package),
  `validator_version` (e.g. `"THR-055/1.0.0"`), and `validator_key`
  (stable deterministic identifier).
- Each validation run creates a distinct event row (distinct run/event
  identifier). Re-runs append new events; never overwrite history.
- Different validators produce distinct recorded results with no silent
  decision-status changes.

---

## 2. Founder-Only Proposal Review API

All new routes are under `POST/GET /skill-lifecycle/proposals/*` and require
bearer authentication (`_require_human` dependency). Agent callers receive
403 on every review endpoint.

### 2.1 Queue — `GET /skill-lifecycle/proposals/queue`

Founder-only, paginated/filterable. Query params:

**Basic:** `status`, `page`, `page_size`.

**Slice 2A typed server filters** (based on immutable ledger/event facts):
- `validation_outcome`: `"validated"`, `"validation_failed"`, `"unvalidated"`
- `search`: case-insensitive match on skill_id, slug, or name
- `proposer`: exact proposer_agent filter
- `submitted_after` / `submitted_before`: ISO-8601 date bounds on created_at

Returns per-proposal: `version_id`, `skill_id`, `slug`, `name`, `version`,
`content_hash` (immutable), `proposer_agent` (immutable), `claimed_by`
(separate), `proposal_task_id`, `proposal_session_id`, `status` (decision),
`latest_validator_version`, `latest_validator_key`, `permitted_next_action`,
`assigned_agent_count`, `assigned_agents`, `created_at`.

Default ordering: actionable first (`CASE WHEN status NOT IN terminal THEN 0 ELSE 1 END`),
then oldest submission (`created_at ASC`). Terminal statuses: `rejected`,
`published`, `retired`, `rolled_back`, `legacy_quarantined`.

### 2.2 Detail — `GET /skill-lifecycle/proposals/{version_id}`

Founder-only. Returns full detail by immutable version/proposal ID:
- **Slice 2A: Canonical immutable SKILL.md bytes** (`skill_md`) — loaded from
  the ArtifactStore via manifest resolution. Returns `null` safely for
  missing/malformed legacy artifacts; never fabricates bytes or exposes
  arbitrary paths.
- **Slice 2A: Package hash/manifest reference** (`package_members`) — listing
  all members with paths, hashes, artifact keys, and sizes from the manifest.
  Returns `null` whenever `skill_md` is null — both fields derive from the
  same verified immutable provenance snapshot; if any check in the chain
  (content_hash, manifest integrity, member digest) fails, neither field is
  returned.
- **Slice 2A: Creation-event purpose/target-agent data** (`purpose`,
  `target_agent_suggestion`) — extracted from the proposed event metadata.
- Read-only package content/artifact reference (`content_artifact_key`)
- Immutable author (`proposer_agent`, `proposal_task_id`, `proposal_session_id`)
- Optional separate claimant (`claimed_by`, `claimed_at`)
- Full append-only events with actor, time, hash, validator, run, failure, rationale
- Lifecycle decision projection (`status`)
- Assignment projection (`assignments[]`)
- Materialization attempts (`materializations[]`)
- Concurrency marker (`last_event_id`)

### 2.3 State-Changing Actions

All accept `expected_event_id` (concurrency marker from detail). On stale
conflict: HTTP 409, code `stale_concurrency`, with `current_event_id`,
`current_status`, and `expected_event_id` in response.

| Action | Route | Body |
| --- | --- | --- |
| Claim | `POST /proposals/{version_id}/claim` | `expected_event_id` |
| Validate | `POST /proposals/{version_id}/validate` | `validator_version`, `expected_event_id` |
| Submit-Review | `POST /proposals/{version_id}/submit-review` | `expected_event_id`, `intended_audience`, `review_notes` |
| Review | `POST /proposals/{version_id}/review` | `decision`, `rationale`, `expected_event_id` |
| Publish | `POST /proposals/{version_id}/publish` | `approval_event_id`, `expected_event_id` |
| Assign | `POST /proposals/{version_id}/assign` | `agent_name`, `expected_event_id` |
| Rollback | `POST /proposals/{version_id}/rollback` | `reason`, `expected_event_id` |

### 2.4 Existing Routes — Auth Changes

| Route | Old | New |
| --- | --- | --- |
| `GET /skill-lifecycle/{skill_id}` | dual-auth | Founder-only |
| `GET /skill-lifecycle/events/{skill_id}` | dual-auth | Founder-only |
| `GET /skill-lifecycle/catalog/custom` | dual-auth | dual-auth (unchanged — only published) |

---

## 3. TypeScript API Mirror

All Founder-only review endpoints have corresponding functions in
`web/src/lib/api/skillLifecycle.ts`:
- `getProposalsQueue(slug, params?)` — Slice 2A: params now include `validation_outcome`, `search`, `proposer`, `submitted_after`, `submitted_before`
- `getProposalDetail(slug, versionId)` — Slice 2A: response now includes `skill_md`, `package_members`, `purpose`, `target_agent_suggestion`
- `claimProposalV2(slug, versionId, body)`
- `validateProposal(slug, versionId, body)`
- `submitReviewProposal(slug, versionId, body)`
- `reviewProposal(slug, versionId, body)`
- `publishProposal(slug, versionId, body)`
- `assignProposal(slug, versionId, body)`
- `rollbackProposal(slug, versionId, body)`

### 3.1 Hooks Layer (Slice 2B)

Query ownership is in the established skills hooks layer:
- `useProposalDetail(slug, versionId)` in `web/src/hooks/skills.ts` — returns
  a TanStack Query `UseQueryResult<ProposalDetailResponse>`. The page consumes
  this hook; it does NOT directly import `getProposalDetail`, `ApiError`, or
  `useQuery`.
- `ProposalDetailResponse` type is re-exported from `@/hooks/skills` so
  feature compositions never deep-import `@/lib/api/skillLifecycle`.

### 3.2 Pure Mapping Helpers (Slice 2B)

`web/src/features/skills/proposal-detail.ts` provides provider-agnostic
mapping functions: `statusLabel`, `statusTone`, `isPublished`, `isRejected`,
`isTerminal`, `hashDisplay`, `readinessFacts` (backed by response facts —
never status-enum synthesized), `timelineEvents` (with content hash and
metadata facts), `validatorFacts`, `assignmentProjection`,
`materializationProjection` (narrow types; uses `success`/`error_message`/
`created_at`, never `materialized_at` or `pending`), `metadataFacts`,
`hasAssignmentProjection`.

---

## 4. Migration (Slice 2A)

No migration required. Slice 2A is additive read-only enrichment:
- `skill_md`, `package_members`, `purpose`, `target_agent_suggestion` are
  computed from existing ArtifactStore + event data at read time.
- Queue filters are pure SQL WHERE clauses with no schema changes.
- No new columns, no semantic changes to existing columns.

Slice 1 migration (already applied): two additive nullable columns on
`skill_lifecycle_packages`:
```sql
ALTER TABLE skill_lifecycle_packages ADD COLUMN claimed_by TEXT;
ALTER TABLE skill_lifecycle_packages ADD COLUMN claimed_at TEXT;
```

---

## 5. Tests

See `tests/daemon/test_skills_proposal_review.py` (comprehensive suite) covering:

**Slice 1:**
- Agent 403 for all review routes
- Claimant/proposer immutability
- Terminal rejection blocks all mutations
- Queue ordering/filtering (basic)
- Proposal detail fields + concurrency marker
- Stale concurrency 409 with current state
- Validation reproducibility (version, key, hash, distinct event rows)
- Decision status independent of assignment (rollback doesn't mutate package
  status, assign doesn't change status)
- Append-only audit fields
- Legacy route compatibility for founder callers

**Slice 2A:**
- `TestProposalDetail`: SKILL.md bytes from artifact store, purpose/target
  from creation event, package_members from manifest, null safety
- `TestProposalQueueFilters`: proposer, search, validation_outcome,
  date bounds, combined filters, pagination total accuracy, actionable-first
  ordering, invalid validation_outcome rejection
- `TestProposalDetailArtifactSafety`: null skill_md with no org_root,
  safe handling of malformed artifact keys, read calls never append events

---

## 6. UI Slices

### 6.1 Slice 2B — Read-Only Founder Proposal Detail Page (Delivered)

PR #550 delivers a **read-only, static presentation page** at
`/orgs/:slug/skills/proposals/:versionId` with NO state-changing controls.

**Delivered sections:**
- Shell breadcrumb + mono identity/version/full copyable hash
- Readiness strip (backed by response facts: events, assignments,
  materializations, supplied status/decision fields — never status-enum
  synthesized)
- SKILL.md primary pane (read-only, wrapped, copy control with keyboard
  accessibility and aria-live feedback, null warning)
- Evidence rail (purpose, policy class, advisory target, validation facts
  from events)
- Provenance (immutable proposer vs optional Founder claimant, task/session
  provenance, reviewer/publisher facts)
- Append-only audit timeline with event content hash, actor/role/time, and
  safely rendered metadata facts (validator version/key, run identifier,
  failure, rationale)
- Assignment & materialization projection (separate from package decision
  status; uses actual server fields `success`/`error_message`/`created_at`;
  never `materialized_at` or `pending`)
- Guidance-only footer

**State handling:** loading skeleton, 403 Founder-access (no data leak),
404, generic error with Retry, skill_md:null warning, rejected terminal
(view-only), published distinct banner.

**No server/API/schema/auth/permission/token/notification/dependency**
change in Slice 2B.

### 6.2 Slice 3A — Read-Only Founder Proposal Queue Page (Delivered)

PR #551 delivers a **read-only, static queue page** at
`/orgs/:slug/skills/proposals` that consumes the existing server contract
(§2.1, `GET /skill-lifecycle/proposals/queue`) and TypeScript API mirror (§3,
`getProposalsQueue`). No backend route, auth change, or schema migration is
added by this slice.

**Delivered features:**
- **Route:** `/orgs/:slug/skills/proposals` — declared before `skills/:skillId`
  in React Router so "proposals" is not swallowed as a dynamic `:skillId`.
- **Status quick-filter chips:** All, Proposed, Draft, Validated, In Review,
  Approved, Published, Rejected.
- **Free-text search** with server query on Enter.
- **Active filter badges** with per-filter removal and "Clear all".
- **Server-authoritative filtering, total, ordering, and pagination** — only
  supported query params forwarded: `status`, `validation_outcome`
  (`validated`, `validation_failed`, `unvalidated`), `search`, `proposer`,
  `submitted_after`, `submitted_before`, `page`, `page_size`. Never
  re-sorted/re-counted client-side.
- **Read-only rows** — status badge, version, claimant (distinct from
  immutable proposer_agent), validator version/key, submitted date.
- **Deep-link navigation** — each row links to the existing Slice 2B detail
  page at `/orgs/:slug/skills/proposals/:versionId`.
- **Loading/empty/error/403 states** with contextual messages. 403 shows the
  standard error UI; no proposal data, row counts, or filter state is leaked.
- **No mutation controls** — no claim, validate, submit-review, review,
  publish, assign, rollback, or retire buttons. Queue is completely read-only.

**Explicitly omitted (no server filter support):**
- "Any assignment" selector — no corresponding server query param exists.
- "Any use case" selector — no corresponding server query param exists.

**Detail page continuity:** The existing Slice 2B Proposal Detail page
remains byte-for-byte as delivered (Founder 403 no-data-leak, immutable
facts, distinct published/rejected view-only rendering, fact-only
readiness/timeline/projections, no catalog-membership/visibility assertion,
both clipboard accessibility feedback controls). Queue rows deep-link to it.

### 6.3 Deferred UI Slices (explicitly out of scope)

The following surfaces are **explicitly deferred** to later slices:

- **All action/confirmation/stale-refresh mutation UI** — no claim, validate,
  submit-review, review decision, publish, assign, rollback, retire, or
  reopen controls of any kind
- **Mutation controls, editor surface, agent approval UI,
  comments/notes, bulk actions, ranking/sorting UI, recovery/reopen
  affordances**
- **Stale-refresh handling** for concurrent mutations (expected_event_id
  concurrency protection is server-only; UI surface deferred)

**Navigation entry (TASK-4309):** A "Proposals" link is rendered inside the
 Skills Catalog surface (`SkillsPage`) as a peer to the surface's topbar
 controls (`Runtime Validation`, `Add custom skill`), targeting exactly
 `/orgs/:slug/skills/proposals`. There is no global AppShell Sidebar entry for
 proposal review — the canonical entry point is the Skills Catalog surface only.
 The link is visible to authenticated users per existing Skills surface
 conventions; Founder-only authorization is enforced server-side (no
 client-side role gating).

### 6.4 Fidelity Targets

- Proposal detail v3: `THR-055-20260802T015112Z-Skill-Proposal-Review-standalone-3-.html`
- Queue v2: `THR-055-20260802T141333Z-Skill-Proposals-Queue-standalone-2-.html`

### 6.5 Server Lifecycle Contract (Unchanged)

The server lifecycle contract defined in §2 is **not modified** by any UI
slice. All routes, response shapes, auth rules, and agent-submission policies
remain as delivered in Slice 1.
