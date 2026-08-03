# THR-055 Skill Proposal Review — Server Contract (Slice 1 + Slice 2A)

**Status:** Current (2026-08-03) — supersedes §4.5 lifecycle wording in protocol/05c-orchestrator.md for the review surface.

**Approved sources:** TASK-4045 design handoff, TASK-4098 (Slice 2A), protocol/05c-orchestrator.md §4.5.

**Scope:** API-read completion only. Server contract + TypeScript API mirror.
Slice 1: founder-only proposal review routes + concurrency + state machine.
Slice 2A: immutable SKILL.md bytes in detail, typed server filters in queue.
React UI is a later, serial slice.

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

This is an API mirror only — no feature UI in this slice.

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
