# Web KB surface — PR 10 detail spec

**Date:** 2026-05-19
**Status:** Draft, pending implementation plan.
**Tracks:** `docs/superpowers/specs/2026-05-18-web-app-complete-feature-set-design.md` §6.4 (umbrella) and GitHub issue [#20](https://github.com/t-benze/my-opc/issues/20).
**Prereq:** PR 7 (Tasks) merged — supplies `FilterSidebar`, `Drawer`, `IdBadge` with optional `to`, `useDensity`, and the DataContext extension pattern.

## 1. Goal

Replace the KB placeholder shell with a read-only browser that lets the founder search, filter, and read knowledge-base entries from the web app. Cross-link source-task badges into Tasks. Optionally allow founder-authored compose behind an env flag for the cases when the CLI is inconvenient.

Edits, deletes, reindex, and `--as-founder` impersonation stay TTY-gated through the CLI per umbrella §5.6.

## 2. Non-goals

- **Edit / delete / reindex from the browser.** Founder-only mutations that can lose work or rewrite shared state stay in the CLI.
- **Multi-tag boolean filters.** Single-tag click filter matches Tasks' team filter.
- **Server-side search highlighting.** Plain markdown body for v1.
- **SSE.** No KB event stream exists; rely on TanStack `staleTime` + manual invalidations on the optional compose path.
- **Architecture changes to the contract pinning.** The 7 KB paths are already in `INCLUDED_PATHS`; no `lib/api/kb.ts` change is required.

## 3. Architecture

Three layers, matching the PR 7 (Tasks) shape:

1. **`web/src/lib/api/kb.ts`** — already mirrors `src/daemon/routes/kb.py` 1:1. No changes.
2. **`web/src/design-system/providers/` extension:**
   - `DataContext.ts` — add `KbApi` (read hooks + flag-gated `useAddKBEntry`) and `KbRoutes`. Extend `DataContextValue` with `kb: KbApi` and `useKbRoutes: () => KbRoutes`.
   - `_real-kb.ts` — TanStack hooks against `lib/api/kb.ts`. Read slug from `useParams`.
   - `_mock-kb.ts` — canned fixtures for `<PrototypeProvider>` and tests.
   - `_real-routes.ts` — add `useRealKbRoutes` returning `inbox()`, `detail(entry_slug)`, `inboxForOrg(slug)` against `OrgSlugContext`.
   - `_mock-routes.ts` — mock equivalents for the prototype sandbox (no-op `inbox` if KB isn't exposed there).
   - `AppProvider.tsx` and `PrototypeProvider.tsx` — wire the new value bag entries.
3. **`web/src/hooks/kb.ts`** — provider-aware façade. One re-export per `KbApi` member, mirroring `hooks/tasks.ts` exactly. Compositions import only from here.
4. **`web/src/features/kb/`:**
   - `KbPage.tsx` — replaces the placeholder; owns the list + sidebar + (optional) compose button.
   - `KbEntryCard.tsx` — feature-local list row. Stays in `features/` until the rule of three triggers promotion.
   - `KbEntryDetailPane.tsx` — `Drawer` with markdown body + cross-links.
   - `ComposeKbEntryDialog.tsx` — flag-gated write-path.
   - `strings.ts` — copy + a11y labels.
   - `KbPage.test.tsx`, `search.test.tsx`, `write-path.test.tsx` — MSW-backed.

## 4. Data flow

| Sidebar state | Hook called | Server param | Client filter |
|---|---|---|---|
| empty search, no type, no tag | `useKBList()` | — | none |
| empty search, type selected | `useKBList({ type })` | `type` | none |
| empty search, tag selected | `useKBList()` | — | `entry.tags.includes(tag)` |
| empty search, type + tag | `useKBList({ type })` | `type` | `entry.tags.includes(tag)` |
| non-empty search | `useKBSearch(q, { limit: 50 })` | `q` | `type` + `tag` applied client-side over the result list (kept simple; result sets are small) |

`limit: 50` is the same default the CLI uses (`grassland kb search`). The list query holds a 30s staleTime (DataContext default). The compose mutation, when enabled, invalidates `['kb-list', slug]` and `['kb-search', slug]`.

`useKBEntry(entry_slug)` is enabled only when `entry_slug` is in the URL (mirrors `useTask` enablement). The Drawer fetches on mount, with a fast path that hydrates from the list cache if the entry happens to be there (TanStack `select` over the list cache, optional optimization — skip if it complicates the test).

## 5. UI structure

### 5.1 KbPage layout

```
+----------------+----------------------------------------------+
| [ search… ]    |  Knowledge base                  [Compose…]* |
|                |  ──────────────────────────────────────────  |
| TYPES          |  KbEntryCard                                 |
|  [ All ]       |   policy/refund-thresholds                   |
|  precedent     |   Refund authority by tier · precedent · 3d  |
|  guide         |   Tags: policy, finance, customer-care       |
|  sop           |   ──────────────────────────────────────     |
|                |  KbEntryCard                                 |
| TAGS           |   intake/spanish-walk-ins                    |
|  [ All ]       |   …                                          |
|  policy        |                                              |
|  intake        |                                              |
|  routing       |                                              |
+----------------+----------------------------------------------+
```

- Sidebar is a **header search box** rendered above a `FilterSidebar`. Keeping the search input *outside* `FilterSidebar` preserves the pure prop-driven shape of that pattern (no change required there).
- TYPES / TAGS option lists derive from the **unfiltered** entry set (de-duped, alphabetized) — same shape as Tasks' team filter. This keeps the sidebar stable as the user toggles filters. Counts (if shown) come from the *current* result set. The `All` row resets that group.
- `[Compose…]` button is rendered only when `import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true'`.
- Density follows `useDensity()`. Compact drops the card padding from `p-3` to `p-2` and hides the tag chips row.

### 5.2 KbEntryCard

```
slug                                       ← mono_sm, text-fg-muted
Title of the entry · precedent · 3d        ← row: title, type pill, age
policy · finance · customer-care            ← tag chips (hidden in compact)
```

- Click → `Link` to `kbRoutes.detail(entry_slug)`. `active` ring styling when its slug matches the route param (matches `TaskCard`).

### 5.3 KbEntryDetailPane (Drawer)

```
[ X ]   policy/refund-thresholds                         ← slug mono
        Refund authority by tier                         ← h1
        precedent · updated 3d ago · authored_by: founder
        Tags: policy, finance, customer-care
─────────────────────────────────────────────────────────
        <Markdown body={entry.body} />                   ← .gl-prose
─────────────────────────────────────────────────────────
        Source task: TASK-0123 →                         ← IdBadge to=…
        Related entries:
          • intake/spanish-walk-ins
          • routing/macau-after-hours
```

- Mounts when route is `/orgs/:slug/kb/:entry_slug`. Drawer `onOpenChange(false)` navigates back to `kbRoutes.inbox()`.
- `source_task` → `<IdBadge kind="task" id={entry.source_task} to={useTasksRoutes().detail(entry.source_task)} />`. The cross-link is the umbrella spec's explicit motivation for the `to` prop landing in PR 7.
- `related_entries` are in-Drawer `<Link>`s to other KB entries; clicking re-targets the route param so the Drawer swaps body.

### 5.4 ComposeKbEntryDialog (flag-gated)

- Trigger: `[Compose…]` button visible only when `VITE_ENABLE_KB_COMPOSE === 'true'`.
- `Dialog` with fields: `slug` (required, kebab + slashes ok), `title`, `type` (free text — backend treats `type` as freeform per `protocol/06-knowledge-base.md`), `topic`, `tags` (comma-split → array), `body` (textarea, monospace; markdown allowed), `related_entries` (comma-split), `source_task` (optional `TASK-NNN`).
- `agent` field is hardcoded to `"founder"` in the request body. `--as-founder` impersonation is **only** required by the daemon for `DELETE`; `POST /kb` accepts any non-empty `agent`. No founder-impersonation surface is added here.
- On success: invalidate KB list/search queries, navigate to `kbRoutes.detail(newEntry.slug)`.

### 5.5 Routing

`web/src/routes.tsx`:

```tsx
<Route path="kb" element={<KbPage />} />
<Route path="kb/:entry_slug" element={<KbPage />} />
```

`KbPage` reads `useParams<{ entry_slug?: string }>()` and renders the Drawer when present (same pattern as Tasks).

### 5.6 Jump key

`TopBar.tsx` already calls `useGlobalJump('t', …)`. Add `useGlobalJump('k', …)` pointing at `kbRoutes.inboxForOrg(activeSlug)`. The umbrella spec's PR 13 polish list owns the audit, but landing `g k` here is consistent with how `g t` shipped in PR 7.

## 6. Files touched

```
M web/src/design-system/providers/DataContext.ts
A web/src/design-system/providers/_real-kb.ts
A web/src/design-system/providers/_mock-kb.ts
M web/src/design-system/providers/_real-routes.ts
M web/src/design-system/providers/_mock-routes.ts
M web/src/design-system/providers/AppProvider.tsx
M web/src/design-system/providers/PrototypeProvider.tsx
A web/src/hooks/kb.ts
M web/src/features/kb/KbPage.tsx
A web/src/features/kb/KbEntryCard.tsx
A web/src/features/kb/KbEntryDetailPane.tsx
A web/src/features/kb/ComposeKbEntryDialog.tsx
A web/src/features/kb/strings.ts
A web/src/features/kb/KbPage.test.tsx
A web/src/features/kb/search.test.tsx
A web/src/features/kb/write-path.test.tsx
A web/src/mocks/kb.ts
M web/src/routes.tsx
M web/src/design-system/layouts/AppShell/TopBar.tsx
M web/UI_SPEC.md
```

## 7. Tests

- **`KbPage.test.tsx`** — MSW:
  - renders list from `GET /kb`;
  - clicking a type pill re-issues `GET /kb?type=<x>`;
  - clicking a tag pill filters client-side;
  - clicking a row opens Drawer + fetches `GET /kb/{slug}`;
  - source-task badge `href` points at `/orgs/:slug/tasks/:task_id`.
- **`search.test.tsx`** — typing in the search box switches the active query from `GET /kb` to `GET /kb/search?q=…` (debounced 200ms).
- **`write-path.test.tsx`** — when `VITE_ENABLE_KB_COMPOSE` is on, compose dialog submits `POST /kb` with the right payload (including `agent: "founder"`), invalidates list cache, and navigates to detail. When the flag is off, the button is absent and the test is `describe.skip`-ped.
- **`openapi-coverage.test.ts`** — no change; the 7 KB paths are already in `INCLUDED_PATHS`.
- **`tests/contract/test_openapi_snapshot.py`** — no change.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Tag list derived from current entries would collapse to the chosen tag when the user filters. | Derive tag and type lists from the **unfiltered** entry set (§5.1) — same shape as Tasks' team filter. |
| Search query churn on each keystroke. | Debounce 200ms in `KbPage` (`useDeferredValue` or a small ref). |
| `useKBSearch` ignoring server-side `type` filter. | Apply `type` and `tag` client-side over the search result set. Documented in §4. |
| Drawer route swap dropping focus. | Mirror `TaskDetailPane`'s focus management — `Drawer`'s shadcn implementation already restores focus to the trigger. New patterns are not introduced; rely on existing behavior. |
| `VITE_ENABLE_KB_COMPOSE` accidentally on in production. | Default is unset → falsy. Production `scripts/build_web.sh` does not set it. Document the flag in `web/UI_SPEC.md` §9. |

## 9. Out of scope (explicit deferral)

- Promoting `KbEntryCard` to `design-system/patterns/` (rule of three).
- Bulk select / multi-tag filter / saved-filter persistence.
- Server-side text highlighting in search results.
- Inline editing or `Compose…`-from-task-detail shortcut.
- KB index regeneration UI (`POST /kb/reindex` button).
- Audit/talks/dashboard deep-links into KB (lands when those surfaces ship).
