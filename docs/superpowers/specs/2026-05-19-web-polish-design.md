# Web — Polish (density / theme / Cmd-K / jump-keys / a11y)

**Date:** 2026-05-19
**Status:** Draft, pending implementation.
**Extends:** `docs/superpowers/specs/2026-05-18-web-app-complete-feature-set-design.md` §6.7 (umbrella).
**Tracks:** GitHub issue #23 (PR 13 — Polish).

## 1. Goal

Take every cross-cutting affordance the umbrella reserved for PR 13 and
make it actually work: light theme is reachable, density is reachable,
`?` opens one help drawer everywhere, `Cmd-K` jumps the founder to any
entity in two keystrokes, and the jump-key map matches the canonical
table in §5.4 of the umbrella. No new daemon endpoints, no new SSE
streams, no new feature pages.

## 2. Non-goals

- **New daemon work.** Every source the palette indexes is already
  mirrored under `lib/api/`. No new routes, no new params.
- **Saved filters / per-user view preferences** beyond density + theme
  (locked by umbrella §2).
- **Light-mode pixel calibration.** Tokens already define both palettes
  via `data-theme="light"`. PR 13 wires the swap. If a particular
  surface (e.g. dense audit row) looks rough in light, ship a calibration
  follow-up rather than expanding scope here.
- **Mobile / responsive layouts** (umbrella §2).
- **Multi-key chords beyond `g <letter>`.** Cmd-K is a single chord. We
  do not introduce per-feature `Cmd-Shift-*` shortcuts in v1.
- **Server-side search for the palette.** Indexing is purely
  client-side over already-loaded React Query caches. We do not call
  `/kb/search` or any new endpoint when typing in the palette.
- **Custom keybindings UI.** The keyboard map is hardcoded; rebinding is
  a follow-up.

## 3. Theme

### 3.1 `useTheme()` hook

`web/src/hooks/theme.ts`. Same shape as `useDensity`:

```ts
export type Theme = 'dark' | 'light';
export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void };
```

- `localStorage["happyranch.theme"]`, default `'dark'`.
- On change, writes `<html data-theme="light|dark">` so CSS overrides
  flip immediately.
- Subscribes to the `storage` event so two open tabs stay in sync (the
  founder may have `/dashboard` and `/threads` open side-by-side).

### 3.2 CSS swap

`tokens.css` keeps the dark palette as the default at `:root`. PR 13
adds a `:root[data-theme="light"]` block that overrides the
`--color-*` tokens with light-mode hex values. Token names do not
change — only their values. Components keep using `bg-surface-canvas`,
`text-text-primary`, etc., and the override flips them in one place.

Light palette (initial values; can be tuned in a follow-up without
touching component code):

| Token | Dark | Light |
|---|---|---|
| `--color-surface-canvas` | `#0c0d0f` | `#f7f8fa` |
| `--color-surface-sunken` | `#121317` | `#eef0f4` |
| `--color-surface-raised` | `#171920` | `#ffffff` |
| `--color-surface-overlay` | `#1d1f27` | `#ffffff` |
| `--color-text-primary` | `#e7e8ec` | `#0c0d0f` |
| `--color-text-secondary` | `#9aa0ac` | `#3e424b` |
| `--color-text-muted` | `#6d7280` | `#6d7280` |
| `--color-border-default` | `#262932` | `#d8dbe2` |
| `--color-border-strong` | `#363a45` | `#b9bdc6` |
| `--color-bg` (v3 alias) | `#0b0d10` | `#f7f8fa` |
| `--color-bg-subtle` (v3 alias) | `#11141a` | `#eef0f4` |
| `--color-bg-raised` (v3 alias) | `#1a1d22` | `#ffffff` |
| `--color-fg` (v3 alias) | `#e6e6e6` | `#0c0d0f` |
| `--color-fg-muted` (v3 alias) | `#9ba1ab` | `#3e424b` |
| `--color-fg-subtle` (v3 alias) | `#6b7280` | `#6d7280` |
| `--color-border` (v3 alias) | `#262a31` | `#d8dbe2` |
| `--color-border-subtle` (v3 alias) | `#1f2229` | `#e3e6ec` |
| `--color-text-inverse` | `#121317` | `#ffffff` |

Accent and tier colors stay the same hex in both modes — they sit on
either surface without legibility issues. The `_tint` companions also
stay the same (the rgba alpha gives them mode-neutral contrast).

### 3.3 TopBar button

Replaces today's empty right-edge slot. Ghost icon button, 28×28px,
`aria-label="Toggle theme"`. Renders sun glyph when current theme is
dark (clicking switches to light) and moon glyph when current is light.
Inline SVG to avoid adding `lucide-react` import overhead just for two
icons.

## 4. Density toggle

Hook already exists (`useDensity`). PR 13 only adds the trigger.

TopBar ghost icon button next to theme toggle. `aria-label="Toggle
density"`. Renders the four-bar `≣` glyph when density is comfortable
(clicking compacts) and the three-bar `≡` glyph when compact (clicking
relaxes). Inline SVG, same sizing as the theme button.

Every existing dense surface (TasksPage, KbPage, AuditPage,
AgentsPage) already calls `useDensity()`. PR 13 audits each and adds
the hook to any newly added row that should honor it — currently none
beyond what PR 7–12 shipped. The DashboardPage (PR 12) re-uses
`TaskCard` with hard-coded `density='compact'` for the "pending your
action" queue; that decision stays since the card is intentionally
denser than the global setting (it shows up to 5 escalations in a
single panel and the founder is scanning, not reading).

## 5. Jump-keys

Final canonical map (matches umbrella §5.4, with `g d` added now that
the dashboard route is real):

| Combo | Target route |
|---|---|
| `g d` | `/orgs/:slug/dashboard` |
| `g i` | `/orgs/:slug/threads` |
| `g t` | `/orgs/:slug/tasks` |
| `g k` | `/orgs/:slug/kb` |
| `g l` | `/orgs/:slug/talks` |
| `g a` | `/orgs/:slug/audit` |
| `g g` | `/orgs/:slug/agents` |

`g i` and `g d` are NEW in PR 13. `g t / g k / g l / g a / g g`
already wired in `TopBar`. The buffer (1.0s) and editable-focus
suppression rules from `useGlobalJump` are unchanged.

`g d` is a deliberate addition not in the umbrella — the dashboard PR
landed after the umbrella and the umbrella explicitly left a follow-up
slot for it. No other letter conflicts.

## 6. Cmd-K command palette

A modal palette that lets the founder jump to any entity in two
keystrokes (`Cmd-K` then a few characters then `Enter`).

### 6.1 Trigger

`Cmd-K` (mac) or `Ctrl-K` (win/linux), wired in the AppShell layout so
it works on every screen. Suppressed when focus is inside an `input`,
`textarea`, or `[contenteditable]` (the same rule `useGlobalJump`
applies).

### 6.2 Layout

Centered modal Dialog, 480px wide, 60vh max-height:

```
+----------------------------------------------+
| 🔎 Search threads, tasks, agents, orgs, KB…  |
+----------------------------------------------+
| THREADS                                      |
|   THR-0123 · Hong Kong visa guide            |
|   THR-0118 · Macau ferry timetable           |
| TASKS                                        |
|   TASK-4421 · Refresh hotel partner list     |
| AGENTS                                       |
|   ● content_writer                           |
| KB                                           |
|   hk-visa-rules · Hong Kong visa rules       |
| ORGS                                         |
|   hk-macau-tourism                           |
+----------------------------------------------+
  ↑↓ navigate    ⏎ open    esc close
```

### 6.3 Indexing

Sources, all pulled from React Query caches that are already populated
by the surrounding session (no new fetches kicked off by the palette):

| Source | Hook | Fields indexed |
|---|---|---|
| Orgs | `useOrgsList()` | `slug`, `display_name` |
| Threads | `useThreadsList(slug, {})` (combined statuses) | `thread_id`, `subject` |
| Tasks | `useTasksList(slug, {})` | `task_id`, `brief` |
| Agents | `useAgentsList(slug)` | `name`, `team` |
| KB | `useKbList(slug)` | `slug`, `title` |

When a hook hasn't been loaded yet (e.g. founder opens the palette
without ever visiting Threads), the palette still mounts and shows the
sections that have data. Empty sections are hidden. The palette does
NOT trigger fetches — it shows what's in cache. This keeps the keystroke
cost flat and avoids surprising the daemon with five concurrent loads
the first time `Cmd-K` is hit. A future PR can opt into proactive
warming if it proves useful.

### 6.4 Matching

Pure client-side substring match, case-insensitive, over each item's
indexable fields concatenated with `· ` separators. No fuzzy library;
the dataset is small (hundreds of items per source at most) and exact
substring is what founders type. Empty query shows the top 5 of each
section.

Result rendering: per-section header (overline-weight muted), items
beneath, max 5 per section. Active row highlighted via `accent.muted`
background.

### 6.5 Keyboard

| Key | Action |
|---|---|
| `↑` / `↓` | Move selection |
| `Enter` | Open selected item |
| `Esc` | Close palette |
| `Cmd-K` / `Ctrl-K` | Toggle (open if closed, close if open) |

Opening a result navigates to:

- Orgs → `/orgs/:slug/threads` (mirrors what the OrgSwitcher does).
- Threads → `/orgs/:slug/threads/:thread_id`.
- Tasks → `/orgs/:slug/tasks/:task_id`.
- Agents → `/orgs/:slug/agents/:agent_name`.
- KB → `/orgs/:slug/kb/:entry_slug`.

All routes use the active slug from `useOrgSlugOptional()` / URL params.
For org results, the slug is the result itself.

### 6.6 Files

- New pattern: `web/src/design-system/patterns/CommandPalette.tsx`
  (pure presentation — accepts `open`, `onClose`, `sections`,
  `onSelect`).
- New host: `web/src/host/CommandPaletteHost.tsx` (gathers data, wires
  the hotkey, mounts inside AppShell, owns the open state). Lives
  outside `src/features/` so it can read each domain's React Query
  cache without violating the cross-feature import boundary
  (`eslint.config.js` no-restricted-imports).
- Hook: `web/src/hooks/command-palette.ts` — `useCommandPaletteHotkey`
  that listens for `Cmd-K` / `Ctrl-K`.

The palette pattern stays free of daemon imports so it can be exercised
in the design-system route with mock sections.

## 7. HelpDrawer tabbed by feature

Today `HelpSheet` accepts a single `shortcuts` list. Threads is the
only consumer. PR 13 extends it to take an array of tabbed sections and
mounts ONE global instance bound to the `?` key.

### 7.1 Shape

```ts
export interface ShortcutSection {
  /** Tab title — "Global", "Threads", "Tasks", ... */
  label: string;
  shortcuts: ShortcutEntry[];
}

interface HelpSheetProps {
  open: boolean;
  onClose: () => void;
  sections: ShortcutSection[];
  /** Tab to show on open. Defaults to "Global". */
  defaultTab?: string;
  footnote?: string;
}
```

Backwards-compat: the old `shortcuts: ShortcutEntry[]` prop stays
supported and renders as a single un-tabbed section so the existing
threads composition keeps working without code churn. The new tabbed
mode activates whenever `sections` is provided (mutually exclusive with
`shortcuts`).

### 7.2 Tabs

Eight tabs in this order: Global, Dashboard, Threads, Tasks, KB,
Agents, Audit, Talks. Each feature owns a `*-shortcuts.ts` file
exporting a `ShortcutEntry[]`. Global covers the cross-cutting binds
(`g <letter>`, `Cmd-K`, `?`, `Esc`).

### 7.3 Global trigger

A new `web/src/host/HelpDrawerHost.tsx` component mounts inside
AppShell, owns the shared open state, listens for `?` (suppressed when
in editable focus), and feeds the tabbed sections to `HelpSheet`.
Pages no longer wire their own `?`-key handler — the previous
per-feature trigger inside `ThreadsPage` is removed and the threads
shortcuts move into the global host's `sections` prop. This makes
"press ? for help" work identically on every surface (umbrella §6.7).

The host lives in `src/host/` (not `src/features/help/`) so it can
import every feature's `*-shortcuts.ts` file without violating the
cross-feature import lint rule. Each feature page still owns the
truth about its own shortcut list — the host only aggregates.

Tab default tracks the active route via `useLocation()` and
`defaultTabForRoute()`: opening `?` on `/orgs/:slug/tasks` lands on
the Tasks tab; on `/orgs/:slug/dashboard` on Dashboard; otherwise
Global.

## 8. Accessibility sweep

Concrete deltas (everything else already conforms via Radix
primitives):

- TopBar wrapped in `<header role="banner">` (already an
  `<header>` element; adds the explicit role for AT consistency).
- TopBar nav becomes `<nav aria-label="Primary">`.
- Theme + density buttons get `aria-label` + `title`.
- All icon-only buttons in feature pages audited — any missing
  `aria-label` is added (current count: 0 violations beyond what PR 7–12
  already covered; the sweep just verifies).
- The Statusbar (when it ships) gets `role="contentinfo"`. The current
  TopBar-only shell has no Statusbar in this PR — flagged as a
  follow-up.
- Daemon health dot keeps its visual cue; a screen-reader-only span
  next to it reports `daemon connected | daemon reconnecting | daemon
  offline` so AT users hear state changes (only renders when the dot
  exists; not blocked on Statusbar).
- HelpDrawer dialog has `aria-describedby` on its content via the
  Radix Dialog; sections are a `role="tablist"` / `role="tab"` pair.
- CommandPalette result list is a `role="listbox"` with each item
  `role="option"` + `aria-selected`. Input is `role="combobox"` with
  `aria-controls` pointing at the listbox.

The sweep is verification-only for the existing feature pages. If a
real violation surfaces during manual smoke, fix it inline; otherwise
the audit lands as zero-diff confirmation in the PR description.

## 9. Cross-cutting decisions

### 9.1 Where the hosts mount

`CommandPaletteHost` and `HelpDrawerHost` both mount inside the
`AppShell` route element (`web/src/routes.tsx` → the `<AppShell>`
wrapper). Mounting outside `OrgLayout` means they survive org switches
without remounting; mounting inside `AppShell` means they do not render
on prototype/design-system routes.

Both files live under `web/src/host/` — a new top-level directory
reserved for AppShell-level singletons that aggregate across
features. The `host/` tree is exempt from the cross-feature import
ESLint rule (which only fires under `src/features/**`), so the
HelpDrawerHost can pull in every `*-shortcuts.ts` directly. Future
shell-level coordinators (toast queue, SSE-status announcer) land
here too.

### 9.2 Cmd-K vs jump-keys

Both ship. They serve different muscle memories — `g t` for "I always
want Tasks", `Cmd-K` for "I want THR-0123 specifically". The
palette does not subsume the jump map.

### 9.3 Theme persistence per browser

`localStorage["happyranch.theme"]` is per-origin, not per-org. The
founder's preference rides with the browser, not the slug. Same rule as
density.

### 9.4 No new dependencies

`lucide-react` is already in `package.json`; new icons can reuse it.
The palette and tabbed help are built on existing Radix primitives
(Dialog, Tabs). No `cmdk` or `kbar` dependency.

## 10. Testing

- `web/src/hooks/theme.test.ts` — default `dark`, `setTheme` writes
  localStorage + `<html>` attribute.
- `web/src/hooks/command-palette.test.ts` — hotkey fires `onOpen`,
  suppressed in editable, toggles when already open.
- `web/src/design-system/patterns/CommandPalette.test.tsx` — render
  with mock sections, filter narrows, Enter calls `onSelect`.
- `web/src/host/CommandPaletteHost.test.tsx` — cache-only behaviour:
  host mounts with an empty QueryClient and renders the
  "Nothing loaded yet" fallback on open, asserts zero HTTP hits.
- `web/src/design-system/patterns/HelpSheet.test.tsx` — sections prop
  renders tabbed, switches active tab on click, `?` from host opens.
- `web/src/design-system/layouts/AppShell/TopBar.test.tsx` —
  density + theme toggles render, click toggles the underlying
  state (localStorage assertion).
- Existing tests stay green. The threads HelpSheet test updates to
  point at the new host-driven flow.

## 11. Ops

- No daemon redeploy. PR is web-only.
- Manual smoke list:
  - Toggle theme: palette switches without reload, persists on
    refresh.
  - Toggle density: row heights compact on Tasks, Audit, KB,
    Agents.
  - `g i / g d / g t / g k / g l / g a / g g` all jump while not
    in editable focus; suppressed when typing in the filter input.
  - `Cmd-K` opens the palette on every surface; typing narrows;
    Enter on a result lands on the correct route; `Esc` closes.
  - `?` opens the HelpDrawer with the right active tab.

## 12. Out of scope / follow-ups

- Statusbar component (umbrella §1 has the design but it's not on the
  current shell). Adding it is a tiny separate PR.
- Light-theme calibration pass — verify dense rows + status badges
  remain legible. Flag whatever needs darker borders or muted-tint
  adjustments; fix in a follow-up.
- Persistent "recent items" section in the palette — a small RAM-only
  ring buffer keyed by recent navigations. Useful but deferrable; the
  flat top-5 per section ships first.
- Per-org Cmd-K scope when multiple orgs exist — today the palette
  indexes the active org's caches only, which is correct for v1. A
  follow-up could add an "all orgs" mode for runtimes with > 1 org.
