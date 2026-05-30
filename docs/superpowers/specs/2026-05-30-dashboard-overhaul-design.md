# Dashboard overhaul — design spec

**Status:** Draft for review · 2026-05-30
**Supersedes:** `2026-05-19-web-dashboard-design.md` (the original four-card layout)
**Scope:** Replace the web Dashboard surface (`/orgs/{slug}/dashboard`) per the design consultation in chat1–chat3 of the design-overhaul bundle. P0 of the priority list in `Design Review.html §05`.

## 1. Why this exists

The current Dashboard renders four equal-weight cards (System health · Pending your action · Active tasks by team · Blocked tasks). The design review's headline criticism: *"3 of 4 cards are empty states. A healthy day — no escalations, no actives, no blocks — produces a dashboard that looks the same as a brand-new install. The most-visited screen in the product communicates the least."*

This spec replaces that surface with the consultation's chosen direction (Linear-dense, see chat1 line 219+): a "Today" heartbeat + narrative + counters in the primary column, a "Waiting on you" inbox in the right column with inline escalation reply, an "Org pulse" per-team activity panel, a "Recent activity" feed, and an "Updates this week" rail.

The honesty principle established in chat2 and audited in chat3 governs the design: **render only what the orchestrator daemon genuinely knows**. No synthesized failure patterns, no editorial narrative ("ran hot mid-afternoon"), no fake progress signals. Aggregated counts, real audit-row content, and real tier states only.

## 2. Non-goals

- **No prototype harness.** The chosen approach (option C in brainstorming) replaces `features/dashboard/DashboardPage.tsx` directly. The `/__prototypes/dashboard-v2` step is deliberately skipped.
- **No scenario toggle (Calm / Busy / Escalation-Reply).** Those existed in the prototype to demo states side-by-side. The production page judges against live data.
- **No theme toggle work.** Dark stays default; light-mode tokens already exist in `tokens.css` but the toggle UI stays out of scope per `DESIGN_SYSTEM.md §14`.
- **No KB citation tracking, no agent failure-pattern callouts, no brief parser.** Already deferred in chat3 per audit feedback.
- **No keyboard-shortcut layer beyond what already exists.** `j/k` navigation and `⌘K` switcher are existing follow-ups, not this PR.
- **No follow-on surface work in this PR.** Tasks / Threads / Audit / Agents / KB / Talks / Jobs come in separate PRs per `Design Review.html §05`.

## 3. Backend changes

### 3.1 New route

`GET /api/v1/orgs/{slug}/dashboard/summary` — single endpoint, no path params beyond the org slug. No query params in v1.

**Returns:** `DashboardSummaryResponse` (Pydantic v2):

```python
class HeartbeatBucket(BaseModel):
    hour: int  # 0–23, in the founder's local tz at request time
    steps: int  # orchestration steps (session_start + completion_report events) in that hour
    tier: Literal["ok", "warn", "bad"]  # green/yellow/red bucket by failed:total ratio

class NarrativeCounts(BaseModel):
    completed_today: int
    failed_today: int
    escalated_open: int
    kb_added_today: int
    agents_active_now: int  # distinct agents with an unclosed session_start
    spend_today_usd: float  # sum from token_usage table, today's rows

class EscalationRow(BaseModel):
    task_id: str
    agent: str
    team: str
    question: str  # from the escalation audit-row payload, verbatim
    raised_at: datetime
    age_seconds: int

class ActiveByTeam(BaseModel):
    team: str
    count: int
    task_ids: list[str]  # for drilling in; limited to 10

class ActivityRow(BaseModel):
    timestamp: datetime
    who: str  # agent handle or "founder"
    event_kind: str  # raw audit event_kind, untransformed
    task_id: str | None
    verdict: Literal["ok", "fail", "warn"] | None  # only present for completion_report / review_verdict rows

class UpdateRow(BaseModel):
    marker: Literal["add", "warn", "info"]
    text: str  # short composable string e.g. "KB +1" or "Learning promoted to KB"
    meta: str  # the slug, agent name, or task id that the update concerns
    timestamp: datetime

class TeamPulse(BaseModel):
    team: str
    acceptance_pct: int  # 0–100, last 7d (review_verdict=approved / total review_verdicts)
    trend_delta: int  # signed, current 7d acceptance minus previous 7d
    sparkline: list[float]  # 12 weeks of weekly acceptance, oldest first; values in [0.0, 1.0]
    members: int  # active agent count for the team
    lead: str  # the manager agent's handle

class DashboardSummaryResponse(BaseModel):
    heartbeat: list[HeartbeatBucket]  # exactly 24 entries
    narrative_counts: NarrativeCounts
    escalations: list[EscalationRow]  # sorted by raised_at DESC, no limit in v1 (typically <10)
    active_by_team: list[ActiveByTeam]  # sorted by team name
    recent_activity: list[ActivityRow]  # last 6, sorted by timestamp DESC
    updates_this_week: list[UpdateRow]  # last 12, sorted by timestamp DESC
    org_pulse: list[TeamPulse]  # one row per team in teams.yaml; teams with zero activity included with all-zero metrics
    org_age_days: int  # days since the org's first audit row, used for first-run empty state
    server_now: datetime  # so the client can compute relative ages consistently
```

### 3.2 Aggregation module

New file: `src/orchestrator/dashboard_summary.py`. Pure functions, no orchestrator-state mutations.

```python
def heartbeat_24h(db: Database, *, now: datetime) -> list[HeartbeatBucket]:
    """24 buckets, one per hour of the last 24h ending at `now`. Steps = count of
    session_start + completion_report rows. Tier = ok if failed/total < 0.10,
    warn if < 0.30, bad otherwise. Empty hours get tier="ok"."""

def narrative_counts_today(db: Database, *, now: datetime, kb_store: KbStore) -> NarrativeCounts:
    """Today = local midnight to now. Reads tasks (status='completed' / 'failed'),
    tasks where block_kind='escalated', distinct unclosed session_start rows
    from audit_log, and token_usage today. kb_added_today comes from the KB
    store directly (count of entries with created_at >= local midnight), NOT
    from audit_log — KB writes are not audited as a dedicated event_kind."""

def org_pulse_7d(db: Database, *, now: datetime, teams: TeamsRegistry) -> list[TeamPulse]:
    """For each team in teams.yaml: last-7d acceptance pct + trend delta vs prior
    7d + 12-week weekly sparkline. Acceptance = review_verdict='approved' /
    total review_verdicts where the reviewed task's assigned_agent is in the
    team. Empty teams return acceptance=0, trend_delta=0, sparkline=[0]*12."""

def recent_activity(db: Database, *, n: int = 6) -> list[ActivityRow]:
    """Last n audit rows of kind in {session_start, completion_report,
    review_verdict, escalation, escalation_resolved, task_dispatched,
    talk_started, talk_ended, learning_promoted}. Verdict populated from
    decision_json.verdict for review rows and from completion_report's status
    for completion rows. KB writes are excluded because they're not audited;
    KB activity surfaces through updates_this_week instead."""

def updates_this_week(
    db: Database, *, now: datetime, kb_store: KbStore, n: int = 12,
) -> list[UpdateRow]:
    """Composed feed for the last 7d, sorted DESC by timestamp. v1 sources
    (both derivable from existing state):
      - KB entries created this week (marker=add, text="KB +1", meta=slug)
        — read from KB store filesystem, not audit_log
      - learnings promoted to KB this week (marker=info, text="Learning promoted to KB",
        meta=kb_slug) — read from learning_promoted audit rows
    No tier-transition row: the daemon doesn't currently track agent tier
    changes as audit events, so per the honesty audit (§6) the row type is
    excluded from v1."""

def spend_today(db: Database, *, now: datetime) -> float:
    """Sum of token_usage.cost_usd rows where timestamp >= local midnight."""

def org_age_days(db: Database) -> int:
    """Days since MIN(audit_log.timestamp). Returns 0 for empty orgs."""
```

Each function takes a `Database` handle and a `now` clock, never `datetime.now()` directly — keeps tests deterministic.

The route handler in `src/daemon/routes/dashboard.py` orchestrates the six calls, packages the response, and returns it. No additional logic.

### 3.3 No new tables, no migrations

Every aggregation reads existing tables:
- `audit_log` (heartbeat, recent_activity, updates_this_week, org_age_days)
- `tasks` (narrative_counts, escalations, active_by_team)
- `token_usage` (spend_today)
- The teams roster comes from `TeamsRegistry` (in-memory) which reads `teams.yaml`

### 3.4 Performance

- `heartbeat_24h` scans `audit_log` rows with `timestamp >= now - 24h`. The existing index on `audit_log(timestamp)` makes this cheap at any realistic org size. Defensive `LIMIT 50000` in the query as a circuit-breaker.
- `org_pulse_7d` runs one aggregate query per team. For an org with N teams this is O(N) round trips to SQLite — acceptable at single-digit team counts.
- All queries run synchronously inside the route handler. No async, no caching, no SSE. The summary refetches on page mount and on user-triggered refresh only. No automatic polling in v1 — the founder hits refresh or navigates back to the dashboard to update it. TanStack Query `staleTime` set to 30s so the summary stays warm during quick tab-switches but recomputes on a real navigation. Polling (`refetchInterval`) can be wired in a follow-up if the founder asks for live tick-tock.

## 4. Frontend changes

### 4.1 New `lib/api/dashboard.ts`

```typescript
import type { DashboardSummaryResponse } from './types';
import { request } from './client';

export async function getDashboardSummary(orgSlug: string): Promise<DashboardSummaryResponse> {
  return request(`/api/v1/orgs/${orgSlug}/dashboard/summary`);
}
```

Plus the TypeScript types added to `lib/api/types.ts` exactly matching the Pydantic models above (no shape divergence — the OpenAPI snapshot test guards against drift).

### 4.2 Provider plumbing

- `design-system/providers/_real-dashboard.ts` — exports `useDashboardSummary()` calling the lib/api function via TanStack Query.
- `design-system/providers/_mock-dashboard.ts` — returns a static fixture with two scenarios switchable via prototype URL param. Even though we're not building a prototype in this PR, the mock keeps `DataContext` honest and unblocks future visual-regression work.
- `design-system/providers/DataContext.ts` — extend the context shape with `dashboard: DashboardApi`.
- `design-system/providers/AppProvider.tsx` and `PrototypeProvider.tsx` — wire the new key.
- `hooks/dashboard.ts` — public-surface hook that calls `useData().dashboard.useDashboardSummary()`.

### 4.3 `features/dashboard/DashboardPage.tsx` — full rewrite

Replaces the current four-card composition. Single `useDashboardSummary()` call. The page composes:

- **Context strip** (top) — `{date · org_age_days · spend_today · agents_active_now}`. Plain divs and text utility classes; no new pattern.
- **Today panel** — `Heartbeat` (local component) + `NarrativeParagraph` (local component) + counter row
- **Waiting on you panel** — `EscalationInboxRow[]` (local component) OR "All clear" empty state via the existing `EmptyState` pattern
- **Org pulse panel** — `OrgPulseTable` (local component) using the new `Sparkline` pattern
- **Recent activity panel** — plain `<ul>` of audit rows; no new pattern
- **Updates this week panel** — plain `<ul>`; no new pattern

### 4.4 New design-system pattern: `Sparkline`

`design-system/patterns/Sparkline.tsx` — promoted to a pattern from day one because Agents (next PR) needs the same component for the scorecard table.

```typescript
type SparklineProps = {
  data: number[];      // values in [0, 1], length 12 typical, length-agnostic
  width?: number;      // default 64
  height?: number;     // default 16
  variant?: 'default' | 'green' | 'yellow' | 'red';  // tints the stroke
};
export function Sparkline(props: SparklineProps): JSX.Element;

export const meta = {
  name: 'Sparkline',
  layer: 'pattern',
  import: '@/design-system/patterns/Sparkline',
  variants: { variant: ['default', 'green', 'yellow', 'red'] },
  consumes: ['typography.mono_sm', 'colors.semantic.dark.tier'],
  example: "<Sparkline data={[0.8, 0.84, 0.78, 0.82, 0.86, 0.90, 0.88]} />",
} as const;
```

The `meta` export feeds `registry.json` on `npm run build:registry`.

### 4.5 New feature-local components (NOT promoted)

In `features/dashboard/components/`:

- `Heartbeat.tsx` — 24-bar histogram. Props: `{ data: HeartbeatBucket[]; nowIdx: number }`.
- `NarrativeParagraph.tsx` — formats `NarrativeCounts` into a single sentence with semantic spans. Pure formatting; no logic.
- `EscalationInboxRow.tsx` — escalation row with inline reply expander. Props: `{ row: EscalationRow; expanded: boolean; onExpand, onCollapse, onReply, onPromoteToKb }`. State (`reply` text, `promoteToKb` checkbox) lives in the parent page so multiple rows can be in different expansion states without state-sharing bugs.
- `OrgPulseTable.tsx` — per-team table with sparkline + acceptance + trend. Props: `{ rows: TeamPulse[] }`.

These stay local because (a) Dashboard is their only consumer in v1 and (b) the design-system migration plan's promotion rule kicks in only on the third use.

### 4.6 Escalation reply flow

The Design Review §04 Q3 specifies the escalation-as-conversation loop. The
backend route already exists: `POST /api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation`
(handler `resolve_escalation_in_process` in `src/daemon/routes/tasks.py`).
Implementation:

- `EscalationInboxRow` expands inline on click.
- Reply textarea autofocuses.
- "Save reply as KB ruling" checkbox is checked by default (preserves the prototype's choice).
- Submit button calls a new dashboard-feature-local mutation hook that POSTs to the existing route. The dashboard does NOT cross-import the `ResolveEscalationDialog` from `features/tasks/` (forbidden by `ARCHITECTURE.md` boundary rule). If the founder also wants the "promote to KB" side-effect, the mutation makes a second call to `POST /api/v1/orgs/{slug}/kb/entries` after the resolve succeeds — both calls land in the dashboard feature folder's mutation hooks file.
- On success, the row animates out (280ms), the inbox count decrements, an injected "KB +1 just now" row appears optimistically in `updates_this_week` if the founder promoted, and the next refetch confirms it.
- ⌘↵ submits; Esc collapses. Both handled in the row's local `onKeyDown`.

A future PR can promote the resolution flow into a shared pattern, allowing both Tasks and Dashboard to consume it. Out of scope here.

## 5. Empty / error states

| State | What renders | When |
|---|---|---|
| `isLoading` | `loading…` muted text inside the page shell with the panels' titles still showing | First page mount |
| `isError` | `Failed to load dashboard.` in `text-feedback-danger` at the page level | TanStack Query error |
| Empty escalations | The existing `EmptyState` pattern with title "All clear" and the most-recent-resolved summary line | `escalations.length === 0` |
| Brand-new org | `EmptyState` with "Start your first brief" CTA linking to the Tasks compose dialog | `org_age_days === 0` AND all counters are zero |
| Quiet but established org | Heartbeat + narrative still render (likely flat/zero); "All clear" inbox | `org_age_days > 0` AND `narrative_counts.completed_today + failed_today + escalated_open === 0` |

## 6. Honesty audit — what we display vs what the daemon knows

| Display | Source | Honest? |
|---|---|---|
| Heartbeat bar height | `audit_log` row count per hour | yes |
| Heartbeat bar color tier | failed/total ratio per hour (computed at query time) | yes — threshold is a config decision, not interpretation |
| Narrative sentence | `NarrativeCounts` formatted into prose with semantic spans | yes — only counted facts; no "ran hot", "all on PR-review", or "median Wednesday" allowed |
| Counter row (5 stats) | `NarrativeCounts` fields, one-to-one | yes |
| Escalation question text | escalation audit-row payload, verbatim | yes |
| Active by team count | `tasks.status='in_progress' GROUP BY team` | yes |
| Recent activity row | `audit_log` row, untransformed event_kind | yes |
| Updates "KB +1" | KB store filesystem (entries with `created_at` this week) | yes |
| Updates "Learning promoted to KB" | `learning_promoted` audit rows | yes |
| Org pulse sparkline | weekly review_verdict approval ratios | yes — definition codified in `org_pulse_7d` |

If any row in this table flips to "depends" or "no" during implementation, the design says **drop the row**, not invent the data. This is the chat2 / chat3 contract.

## 7. Files touched

**New backend files:**
- `src/daemon/routes/dashboard.py`
- `src/orchestrator/dashboard_summary.py`
- `tests/daemon/test_routes_dashboard.py`
- `tests/orchestrator/test_dashboard_summary.py`

**Modified backend files:**
- `src/daemon/app.py` — register the new router
- `tests/contract/openapi.json` — auto-regenerated by the snapshot test
- `tests/contract/test_openapi_snapshot.py` — no edit; the snapshot regen captures the new path

**New frontend files:**
- `web/src/lib/api/dashboard.ts`
- `web/src/design-system/providers/_real-dashboard.ts`
- `web/src/design-system/providers/_mock-dashboard.ts`
- `web/src/hooks/dashboard.ts`
- `web/src/features/dashboard/components/Heartbeat.tsx`
- `web/src/features/dashboard/components/NarrativeParagraph.tsx`
- `web/src/features/dashboard/components/EscalationInboxRow.tsx`
- `web/src/features/dashboard/components/OrgPulseTable.tsx`
- `web/src/design-system/patterns/Sparkline.tsx`
- `web/src/design-system/patterns/Sparkline.test.tsx`

**Modified frontend files:**
- `web/src/lib/api/types.ts` — add the new response models
- `web/src/lib/api/index.ts` — export the new module
- `web/src/design-system/providers/DataContext.ts` — extend the context shape
- `web/src/design-system/providers/AppProvider.tsx` — wire the real dashboard hook
- `web/src/design-system/providers/PrototypeProvider.tsx` — wire the mock dashboard hook
- `web/src/design-system/registry.json` — regenerated; commits the Sparkline entry
- `web/src/features/dashboard/DashboardPage.tsx` — full rewrite per §4.3
- `web/src/features/dashboard/DashboardPage.test.tsx` — replace existing tests with loading / error / empty / populated branches
- `web/src/features/dashboard/dashboard-shortcuts.ts` — keep but verify it doesn't reference removed elements
- `web/src/test/openapi-coverage.test.ts` — add `/api/v1/orgs/{slug}/dashboard/summary` to `INCLUDED_PATHS`

**Deleted frontend files:** none.

## 8. Testing

**Backend:**
- `test_dashboard_summary.py` — one test per aggregation function, with a seeded in-memory SQLite, deterministic `now`, and a known fixture set. Cover empty-org, single-team, multi-team cases.
- `test_routes_dashboard.py` — happy path (200 + shape), slug resolution (404 for unknown slug), error path (DB read failure surfaces as 500).
- Existing `test_openapi_snapshot.py` regenerates; CI fails until intentional snapshot regen is committed.

**Frontend:**
- `dashboard.test.ts` (lib/api) — round-trip the typed function via fetch mock.
- `Sparkline.test.tsx` — renders for empty / single-value / typical / all-tier variants.
- `DashboardPage.test.tsx` — six branches: loading, error, brand-new-org empty, quiet-org empty, populated calm, populated with-escalations. Each branch asserts a specific text fragment, not full HTML.
- `openapi-coverage.test.ts` — passes only after the new path is added to `INCLUDED_PATHS`.

**Integration:**
- No new integration test. The route is pure aggregation; the orchestrator unit tests cover the logic and the daemon route handler is a thin wrapper.

## 9. Out-of-scope follow-ups that this design touches

These items are flagged elsewhere and would be sequenced after this PR:

1. **Agents profile rebuild (P2).** Will reuse `Sparkline`. Needs the `failKind` vocab decision (see audit report) before implementation.
2. **Escalation resolution route.** Either land it as part of this PR (if the route doesn't exist) or wire the dashboard against the existing route (if it does). Decision deferred to implementation-time discovery.
3. **Tier-change updates feed entry.** Requires agent_performance audit rows that may not exist yet. Drop from `updates_this_week` if not derivable.
4. **Keyboard layer (`j/k` navigation, `⌘K` switcher).** P3 from the design review. Not in this PR.

## 10. Open question(s)

None blocking. Both pre-implementation questions resolved during spec self-review:

1. **Escalation-resolution route** exists at `POST /api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation`. Dashboard wires against it; no placeholder needed.
2. **Agent tier transitions are not audited.** The "tier change" row type is removed from `updates_this_week` per the honesty audit; `learning_promoted` audit rows take its slot.

## 11. Rollout

This PR ships as one atomic change on `worktree-design-overhaul`. The branch already exists; the worktree is clean. No feature flag — the new Dashboard replaces the old one immediately on merge. Rollback is a single revert.

The follow-up PR shape:
- **P1 next:** List density pass (Tasks / Threads / Jobs / KB rows compressed to 44px).
- **P1 next:** Thread transcript polish (per-agent avatars; `kind_tag` system payloads extracted to sidebar).
- **P2:** Agent profile rebuild (consumes `Sparkline`; needs `failKind` decision first).
- **P2:** Audit Activity session-grouping.
- **P3:** Connective tissue (task → spawning thread, thread → spawned tasks, talk → promoted KB rulings).
- **P3:** Keyboard layer.
