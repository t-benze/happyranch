# THR-055 Proposal Review — Server Contract & UI Implementation

## Status

- **Server contract:** Merged (PR #550). Routes live in `runtime/daemon/routes/skill_lifecycle.py`.
- **UI Slices:**
  - **Slice 1 (Catalog):** Delivered.
  - **Slice 2A (Skill Detail):** Delivered.
  - **Slice 2B (Validation):** Delivered.
  - **Slice 3A (Proposal Queue):** ✅ DELIVERED — TASK-4154. Read-only founder queue page at `/orgs/:slug/skills/proposals`. See §5.
  - **Slice 3B (Proposal Detail / Review):** Deferred — lifecycle actions (claim, validate, review, publish, assign, rollback).
  - **Slice 4 (Confirmation / Bulk):** Deferred — confirmation dialogs, bulk decisions, stale-refresh detection.
  - **Slice 5 (Agent-facing):** Deferred — agent proposal submission UI.

## 1. Routes

All lifecycle routes are under `/api/v1/orgs/{slug}/skill-lifecycle/`.

### 1.1 Read (dual-auth — founder bearer OR agent session)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/skill-lifecycle/proposals` | **Founder-only proposal queue** (THR-055 Slice 3A). Returns paginated list of proposals. Query params: `status`, `validation_outcome`, `search`, `proposer`, `submitted_after`, `submitted_before`, `page`, `page_size`. Agent callers receive 403. |
| GET | `/skill-lifecycle/{skill_id}` | Read lifecycle status for one skill. |
| GET | `/skill-lifecycle/catalog/custom` | List published custom skills. |
| GET | `/skill-lifecycle/events/{skill_id}` | Read event history. |

### 1.2 Write (human/founder bearer only)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/skill-lifecycle/proposals` | Human/founder proposal submission. |
| POST | `/skill-lifecycle/proposals/agent` | Agent-only opaque-session proposal. |
| POST | `/skill-lifecycle/{skill_id}/claim` | Claim proposal → draft. |
| POST | `/skill-lifecycle/validate` | Validate a version. |
| POST | `/skill-lifecycle/submit-review` | Submit for review. |
| POST | `/skill-lifecycle/review` | Approve/reject. |
| POST | `/skill-lifecycle/publish` | Publish. |
| POST | `/skill-lifecycle/assign` | Assign to agent. |
| POST | `/skill-lifecycle/rollback` | Emergency rollback. |
| POST | `/skill-lifecycle/retire` | Retire. |

## 2. Queue Endpoint (Slice 3A)

### 2.1 Request

```
GET /api/v1/orgs/{slug}/skill-lifecycle/proposals
Authorization: Bearer <token>  (founder-only — agent → 403)
```

Query parameters (all optional, server-authoritative):

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter by lifecycle status (proposed, draft, validated, in_review, approved, published, rejected) |
| `validation_outcome` | string | Filter by validation outcome (ok, failed) |
| `search` | string | Free-text search across name, description |
| `proposer` | string | Filter by proposer agent name |
| `submitted_after` | string | ISO 8601 lower bound on submitted_at |
| `submitted_before` | string | ISO 8601 upper bound on submitted_at |
| `page` | int | Page number (default 1) |
| `page_size` | int | Items per page (default 20) |

### 2.2 Response

```json
{
  "items": [
    {
      "skill_id": "hr:my-skill",
      "version_id": 42,
      "version": "1.0.0",
      "name": "My Skill",
      "description": "...",
      "status": "proposed",
      "proposer_agent": "dev_agent",
      "claimed_by": null,
      "claimed_at": null,
      "content_hash": "abc123...",
      "submitted_at": "2026-08-01T00:00:00Z",
      "validator_version": null,
      "validator_key": null,
      "validation_outcome": null,
      "permitted_next_action": "claim"
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 20
}
```

Never re-sort/re-count/filter client-side. Ordering is server-authoritative.

## 3. Queue UI (Slice 3A — DELIVERED)

### 3.1 Route

`/orgs/:slug/skills/proposals` — declared before `skills/:skillId` in the React Router tree.

Detail deep-link: `/orgs/:slug/skills/proposals/:versionId` (placeholder page — lifecycle actions deferred to Slice 3B).

### 3.2 Supported Features

- **Header** with "Proposal Queue" title and "Skills · Proposals" overline.
- **Founder-only explanatory panel** with guidance text.
- **Status summary** — status quick-filter chips: All, Proposed, Draft, Validated, In Review, Approved, Published, Rejected.
- **Search** — free-text search input with server query on Enter.
- **Active filter badges** with per-filter removal and "Clear all".
- **Server-authoritative filtering** — only supported params sent; total/count/ordering from response.
- **Pagination** — Previous/Next with page info, disabled at bounds.
- **Loading skeleton** — animated placeholder rows.
- **Empty state** — contextual message (no proposals / no matches).
- **Error state** — message with Retry button.
- **403 state** — no data leak; shows error state like any other failure.
- **Read-only rows** — status badge, version, claimant (distinct from proposer), validator version/key, submitted date.
- **Deep-link navigation** — each row links to `/orgs/:slug/skills/proposals/:versionId`.
- **No mutation buttons** — no claim, review, publish, assign, rollback, retire, validate, or approve buttons.

### 3.3 Explicitly Omitted (Visual-Contract Delta)

The queue-v2 design mockup shows:
- **"Any assignment"** selector — not supported by server API; OMITTED.
- **"Any use case"** selector — not supported by server API; OMITTED.

These are deferred for a future server API extension. The UI preserves layout as reasonably possible without these selectors. A dedicated test proves only supported query params are sent.

### 3.4 Deferred (Slice 3B+)

- Proposal detail page with full lifecycle actions.
- Confirmation dialogs.
- Bulk decision actions.
- Stale-refresh detection.
- Editor / comments / collaboration.
- Ranking / sorting beyond server-provided ordering.
- Notifications.
- Re-opening/recovery after rejection.

## 4. Client API Surface

### 4.1 TypeScript API (`web/src/lib/api/skillLifecycle.ts`)

```typescript
export interface ProposalsQueueParams {
  status?: string;
  validation_outcome?: string;
  search?: string;
  proposer?: string;
  submitted_after?: string;
  submitted_before?: string;
  page?: number;
  page_size?: number;
  [key: string]: string | number | undefined;
}

export interface ProposalQueueItem {
  skill_id: string;
  version_id: number;
  version: string;
  name: string;
  description: string;
  status: string;
  proposer_agent: string | null;
  claimed_by: string | null;
  claimed_at: string | null;
  content_hash: string;
  submitted_at: string;
  validator_version: string | null;
  validator_key: string | null;
  validation_outcome: string | null;
  permitted_next_action: string | null;
}

export interface ProposalsQueueResponse {
  items: ProposalQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export const getProposalsQueue = (
  slug: string,
  params?: ProposalsQueueParams,
): Promise<ProposalsQueueResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals`, { params });
```

### 4.2 Hook (`web/src/hooks/skills.ts`)

```typescript
export const useProposalsQueue = (params) => useData().skills.useProposalsQueue(params);
```

Provider-aware, owns TanStack Query with 15s staleTime, queryKey `['proposals-queue', slug, params]`.

## 5. Test Coverage

- **Unit/page tests:** 17 tests covering initial render, status filter, search, pagination, response total, server ordering, deep-link routing, loading/empty/error/retry, 403 no data leak, terminal read-only rows, claimant/proposer distinction, no unsupported mockup selectors, no mutation API calls.
- **Route precedence:** `/skills/proposals` not swallowed by `skills/:skillId`; coexists with `skills/proposals/:versionId`.
- **Full skills suite:** 175 tests pass across 12 test files.
- **ESLint:** exit code 0 (pre-existing tailwind config warnings only).
- **OpenAPI coverage:** passes (no new endpoint — server route deferred).

## 6. Implementation Notes

- No server/API/schema/migration/auth/permissions change.
- No backend endpoint exists for `GET /skill-lifecycle/proposals` — the frontend API wrapper (`getProposalsQueue`) defines the expected contract shape for a future server implementation. The queue page is fully functional with MSW-mocked data in tests.
- Filter changes issue server queries via URL search params; only documented supported params are forwarded.
- The `permitted_next_action` field from the response is displayed as non-interactive factual text only if returned by the server; it is never used to enable/disable UI controls.
