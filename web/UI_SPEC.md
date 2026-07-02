# UI_SPEC.md — HappyRanch Founder Console

**Companion to:** `web/DESIGN.md` (tokens), `web/ARCHITECTURE.md` (layer rules), `docs/superpowers/specs/2026-05-14-web-ui-design.md` (the original web-UI design doc), `protocol/05e-dashboard.md` (informs the future Audit / Trends surface).

**Status:** v0.1 spec. Threads is implemented; the other four nav slots (Tasks, KB, Audit, Agents) are sketched only to anchor the navigation model.

---

## About this document

A structured-markdown UX specification with one section per screen (Purpose / Layout / States / Interactions / Data / A11y / Empty + error / Keyboard). Token references point at `web/DESIGN.md`. Implementation contract — how the screens become real React code — lives in `web/DESIGN_SYSTEM.md`.

---

## 0. Table of contents

1. App shell (TopBar, Statusbar, nav, org switcher)
2. Threads — Inbox pane
3. Threads — Detail pane
4. Threads — Dialogs (NewThread / Invite / Archive / Abandon)
5. Threads — HelpDrawer
6. Empty states (cross-screen)
7. Error and disconnected states
8. Tasks — placeholder shell
9. KB — placeholder shell
10. Audit — placeholder shell
11. Agents — placeholder shell
12. Global UX — keyboard map, toast queue, theme toggle, density toggle

---

## 1. App shell

### Purpose

Three regions, fixed: a 48px TopBar, a flexible body, a 24px Statusbar. Every screen renders inside the body. The shell carries org identity, daemon status, and primary nav so that no individual feature has to re-implement those.

### Layout

```
+--------------------------------------------------------------------------+
| HappyRanch  [hk-macau-tourism ▾]  Threads · Tasks · KB · Audit · Agents     ⏻ ◐|  48px  TopBar
+--------------------------------------------------------------------------+
|                                                                          |
|                                                                          |
|                          (active feature body)                           |
|                                                                          |
|                                                                          |
+--------------------------------------------------------------------------+
| ● daemon  org: hk-macau-tourism  streams: 2  v0.1.0+a6e654e          ?    |  24px Statusbar
+--------------------------------------------------------------------------+
```

TopBar elements, left to right:
- **Wordmark** "HappyRanch" in `typography.h3` weight 600, tracking -0.005em. Click → routes to `/orgs/:slug/threads` (no separate "home").
- **OrgSwitcher** — native `<select>` themed as a `components.select`, value = active slug. Shows slug only (not display name), mono in the trigger; on click opens to a list of `{slug — display_name}`. If exactly one org, the trigger is read-only static text in `typography.mono_md` colored `accent.default`.
- **Nav row** — five tabs: Threads, Tasks, KB, Audit, Agents. Active tab uses `surface.raised` background + `text.primary`. Disabled tabs (Tasks, KB, Audit, Agents until shipped) use `text.muted` + `cursor: not-allowed` + tooltip "Coming soon."
- **Right slot** — two icon-only ghost buttons: theme toggle (sun/moon, 16px) and density toggle (≡ vs ≣).

Statusbar elements, left to right:
- Daemon connection dot — `tier.green` solid (connected), `tier.yellow` (reconnecting), `tier.red` (offline). Pulses on reconnect via `animate-pulse`, never on connected steady state.
- Org slug in `typography.mono_sm`, prefixed `org:`.
- Active SSE stream count (e.g., `streams: 2`), updates whenever a `useSSE` hook mounts/unmounts.
- Build version: `vX.Y.Z+<git-short-sha>`. Click → opens a tiny popover with full SHA + build date.
- Right edge: a `?` kbd chip — global "press ? for help" cue.

### States

| State | Visual |
|---|---|
| No orgs in container | TopBar shows "HappyRanch", nav disabled, body renders an EmptyState pointing at `happyranch orgs init` |
| One org | OrgSwitcher renders as static slug; no chevron |
| Multiple orgs | OrgSwitcher opens dropdown |
| Daemon offline | TopBar still renders; Statusbar dot turns red; body shows the global disconnected screen (§7) |
| Theme = light | Sun icon highlighted, palette swaps to `colors.semantic.light` |

### Interactions

- Cmd/Ctrl-K (future) — opens a command palette (deferred; flagged in §12).
- Tab cycles through TopBar then into the active feature body. The active nav item has `aria-current="page"`.
- Switching org via the dropdown replaces `:slug` in the URL and remounts the feature. We do NOT preserve thread selection across orgs — that would surface a thread that doesn't exist in the new scope.

### Data dependencies

- `GET /api/v1/orgs` → OrgSwitcher.
- `GET /api/v1/auth/bootstrap` (once, on mount) → token store.
- `GET /api/v1/health` polled every 5s → Statusbar daemon dot.

### A11y

- TopBar is a `<header role="banner">`. Nav is a `<nav aria-label="Primary">`.
- Statusbar is `<footer role="contentinfo">`. The daemon dot has `aria-live="polite"` on its label so screen readers announce state changes.
- All icon-only buttons have `aria-label`.

### Keyboard

| Key | Action |
|---|---|
| `g i` | Jump to Threads inbox |
| `?` | Open HelpDrawer (handled per-feature; global no-op when no feature owns it) |
| `Esc` | Close any open drawer/dialog/toast |

---

## 2. Threads — Inbox pane

### Purpose

The founder's primary surface. Email-client inbox: list of threads grouped by status, scannable in under a second, keyboard-first. This is where most session-time is spent.

### Layout (340px column)

```
+--------------------------------------+
| INBOX                       [+ New]  |  ← overline + accent button, 24px tall
| [Filter…                          ]  |  ← compact input, body_sm
| [ open · archived · abandoned ]      |  ← status tabs row, body_sm pills
+--------------------------------------+
| ● Subject of an open thread          |  ← needs-you dot prefix
|   THR-0123  · content_writer · 3h    |     mono id · last speaker · age
+--------------------------------------+
|   Another thread subject             |  ← no dot — last msg not @founder
|   THR-0122  · ops_manager   · 1d     |
+--------------------------------------+
|   archived subject (greyed)          |  ← only in the archived tab
|   THR-0118  · founder       · 4d     |
+--------------------------------------+
| … virtualized rows scroll …          |
+--------------------------------------+
```

Tokens used: `surface.sunken` background, `border.subtle` divider on the right edge, `inbox_row` token block, `typography.overline` for INBOX header.

### Row anatomy (InboxRow)

Two-line row, 44px tall in comfortable density, 32px in compact:

- **Line 1 (subject line):** optional 6px `accent.default` dot (the "needs you" marker — last message's `addressed_to` includes founder OR is `@all`), then subject in `body_sm` weight 500 truncated with ellipsis, then on the right edge a status `Badge` (open / archiving / archived / abandoned) — *only when status ≠ "open"*. In the default open-only view the badge is omitted.
- **Line 2 (meta):** monospace thread id (`id_thread` badge, no fill), then `·`, then last speaker name as an AgentChip (no dot if the speaker is the founder; dot + name otherwise), then `·`, then relative age ("3h", "1d", "Apr 11"). All in `caption` size, `text.muted`.

A row is `active` when `:thread_id` matches its id: gets `accent.muted` background + 2px `accent.default` left-edge marker. Hover state is `surface.raised` with no marker.

### States

| State | Visual |
|---|---|
| Loading | "Loading…" placeholder in `text.muted`, no skeleton — list arrives in one batch |
| Error | "Failed to load threads." in `tier.red`, `body_sm` |
| Empty (no threads, no filter) | EmptyState "No threads yet. Press N to compose." |
| Empty (filter typed, no matches) | "No threads match the filter." in `text.muted` |
| Stale (no SSE invalidation in >60s) | Statusbar `streams` count drops; rows render as-is — no per-row badge |

### Interactions

- Click anywhere on a row → navigate `/orgs/:slug/threads/:thread_id`. The whole row is one `<NavLink>`.
- Type into Filter input → client-side substring match against subject AND thread_id. Debounce 0ms — the list is small (hundreds at most).
- Status tabs swap query key (`status: open|archived|abandoned`). Selected tab uses `surface.raised`, inactive uses `text.muted`.
- "+ New" button → opens NewThreadDialog (§4.1). Hotkey: `N`.

### Data dependencies

- `GET /threads?status=...` via `useThreadsList(slug, { status })`.
- `GET /threads/events` SSE via `useThreadsInboxSSE(slug)` → invalidates the list cache.

### A11y

- The list is an `<ul role="list">` with each row an `<li>`. The NavLink inside has the accessible name "Thread {subject}, {speaker}, {relative-age}." — read in one breath.
- The needs-you dot has `aria-label="addressed to you"` and a `title` for sighted users.
- Status tabs are a `<div role="tablist">` with `aria-selected`.

### Keyboard

| Key | Action |
|---|---|
| `j` / `k` | Move selection down / up (future — not in v0.1) |
| `Enter` | Open selected row (future) |
| `/` | Focus the Filter input |
| `N` | Open NewThreadDialog |

---

## 3. Threads — Detail pane

### Purpose

Read messages, send a reply, manage thread lifecycle (invite, archive, abandon). Right column, 1fr, fills remaining width.

### Layout

```
+------------------------------------------------------------------+
| Subject of the thread          [open]      [Invite] [A]         |  ThreadHeader, ~64px
| THR-0123 · founder, content_writer, content_manager              |
+------------------------------------------------------------------+
|                                                                  |
|  ● content_writer                              #1 · Apr 11 14:32 |  MessageBubble (worker)
|  Draft: Hong Kong visa guide v2.                                 |
|  Please review for currency-policy section.                      |
|                                                                  |
|        · invited content_qa · #2 · Apr 11 14:34 ·                 |  system event (pill)
|                                                                  |
|  ◆ founder → @all                              #3 · Apr 11 14:40 |  MessageBubble (founder)
|  Approved.  Ship it.                                             |
|                                                                  |
|  ● content_qa                                  #4 · Apr 11 14:42 |
|  Declined: section 4 still cites the wrong fee.                   |
|                                                                  |
+------------------------------------------------------------------+
| [textarea, 4 rows, body_lg, 72ch measure, sticky bottom]         |  Composer
| Sends as founder; @all by default.            [Send Ctrl+Enter] |
+------------------------------------------------------------------+
```

### ThreadHeader anatomy

- Line 1, left: subject in `h2`, with a status `Badge` (open / archiving / archived / abandoned) immediately after. Right: action button row — Invite, Archive (all `ghost`).
- Line 2, all `caption` size, all `text.muted`: monospace `THR-NNN`, then `·`, then participant AgentChips (comma-separated, with role dots).
- Optional row 3: if `archive_summary` present, render in a `surface.raised` box with a `text.primary` "Archive summary:" lead-in.

When status ≠ "open", all action buttons are disabled.

### MessageList anatomy

Vertical stack, gap = `spacing.3`. Three bubble shapes:

- **Agent message (worker or manager):** standard `MessageBubble` — `surface.raised` background, `border.subtle` 1px, `radius.lg`. Header line: AgentChip (with role dot) + optional `→ addressed_to` chip-list in `text.muted` + right-aligned `#{seq} · {ts}` in `mono_sm`. Body: markdown in `body_lg` with `prose` styling. Max width 72ch, left-aligned.
- **Founder message:** same as agent but with `founder` variant — `accent.muted` background, accent-tinted border. The AgentChip dot uses the founder accent color. Bubble is still left-aligned (no chat-app "your messages on the right" — this is a record of decisions, not a conversation).
- **Decline:** `tier.red_tint` background, red-tinted border, body renders as `Declined: {reason}` in `tier.red` text.
- **System event:** pill-shaped, dashed border, centered horizontally, `caption` size, format `[seq] {description} · {ts}` — invitations, archive requests, turn-cap extensions.

Auto-scroll to bottom on message-count change. The scroll respects user-initiated scroll-up (don't yank back if they're reading history) — implemented with a "scrolled-up" sentinel and a floating "↓ Jump to latest" button at elevation 2.

### Composer anatomy

- Sticky bottom bar, `surface.sunken` background, `border.subtle` top border, `spacing.3` padding.
- One `textarea`: 4 rows default, auto-grows to 8 rows max then scrolls, measure 72ch, placeholder "Write a message… Ctrl+Enter to send."
- Below the textarea: a meta row. Left: helper text "Sends as **founder**; @all by default." OR error message in `tier.red`. Right: `Send` primary Button.
- Disabled when thread status ≠ "open" — placeholder changes to "Thread is closed."

Future-extension: a `To:` chip-picker right above the Send button, constrained to current participants, defaulting to `@all`. Not in v0.1.

### States

| State | Visual |
|---|---|
| No thread selected | EmptyState "Select a thread from the inbox. Press `N` to compose." with the kbd hint baked in |
| Loading | Centered "Loading…" in `text.muted` |
| Error | Centered "Failed to load thread." in `tier.red`, with a "Retry" ghost button |
| Closed (archived/abandoned) | All action buttons disabled, composer disabled with placeholder. The Archive summary block (if any) is the most visually weighted row. |
| Sending a reply | Send button shows "Sending…" with disabled state; composer disabled until response |
| SSE dropped | Statusbar dot turns yellow; the detail pane silently retries; no banner in the detail pane itself |

### Interactions

- Clicking an AgentChip in the participant list → opens an "Agent detail" Drawer (future). v0.1: AgentChip is non-interactive in the detail pane.
- Ctrl/Cmd-Enter in the composer → submits. Plain Enter inserts a newline (markdown).
- Send error → inline error in the meta row + a toast.

### Data dependencies

- `GET /threads/{id}` via `useThread(slug, threadId)` — header + initial messages.
- `GET /threads/{id}/tail?since_seq=N` SSE via `useThreadTailSSE` — appends.
- `POST /threads/{id}/send` via `useSendFollowUp` — composer submit.
- All three dialog mutations (`/invite`, `/archive`, `/abandon`) — see §4.

### A11y

- The message list is `<ol role="list">` with `aria-label="Thread messages"`. Each bubble is an `<li>`. The seq is read first ("Message 3, from founder, addressed to @all, sent April 11 14:40, …") via an `aria-label` on the bubble.
- The composer textarea has `aria-label="Compose follow-up"`. The send button is `aria-keyshortcuts="Control+Enter Meta+Enter"`.

### Keyboard

| Key | Action |
|---|---|
| `R` | Focus the composer |
| `I` | Open Invite dialog |
| `A` | Open Archive dialog |
| `X` | Open Abandon dialog |
| `F` | Open NewThreadDialog prefilled as forward |
| `Ctrl/Cmd+Enter` | Submit composer |
| `Esc` | If a dialog is open, close it; otherwise no-op |

---

## 4. Threads — Dialogs

All dialogs use the `dialog` component token (elevation 3, 32rem max width, scrim backdrop, `Esc` to close, click-outside to close). Each has its own validation, mutation, and error story.

### 4.1 NewThreadDialog

**Title:** "New thread" or "Forward thread" (if prefilled with `forwarded_from_id`).

**Fields:**
- `Subject` — single-line input, `autoFocus`, required.
- `Recipients (comma-separated agent names)` — single-line input, required. Future: chip-picker with autocomplete from `GET /agents`.
- `Body (Markdown)` — textarea, 6 rows, `body_lg`, required.

**Footer:** Cancel (`ghost`) + Send (`primary`, disabled while pending, text "Sending…" when in-flight).

**Validation:** All three required. Inline error message above the footer, `tier.red`, body_sm: "Subject, recipients, and body are all required."

**Submit:** Ctrl/Cmd+Enter in any field also submits. On success: dialog closes, parent navigates to the new thread. On API error: error message replaces the validation message; dialog stays open with values preserved.

### 4.2 InviteDialog

**Title:** "Invite participant"

**Fields:**
- `Agent name` — input, required, `autoFocus`. Future: autocomplete.

**Footer:** Cancel + Invite.

**Validation:** Non-empty agent name. On API error: same inline-error pattern.

### 4.3 ArchiveDialog

**Title:** "Archive thread"

**Fields:**
- `Summary` — textarea, 4 rows, `body`. Optional but recommended.
- Checkbox: "Request close-out from participants" (default **checked**).

**Footer:** Cancel + Archive (`primary`, NOT `destructive_filled` — archival is non-destructive).

**Validation:** None — empty summary is allowed, just discouraged. A `caption`-size hint below the textarea: "Participants will be asked to file their close-outs in the next turn."

### 4.4 AbandonDialog

**Title:** "Abandon thread"

**Fields:**
- `Reason` — textarea, 4 rows. Required.

**Footer:** Cancel + Abandon thread (`destructive_filled`, the only red filled button in the product).

**Confirmation:** Above the footer, a `caption` warning in `tier.red`: "Abandoning is irreversible. Use Archive if the thread completed successfully."

## 5. Threads — HelpDrawer

### Purpose

Cheat sheet for keyboard shortcuts. Triggered by `?`.

### Layout

A `dialog`-style modal (not a side drawer in v0.1 — the name "HelpDrawer" is historical; we keep the name in code but it renders as a modal). Title: "Keyboard shortcuts." Body: a two-column list of KbdChips + description text, gap = `spacing.2`.

```
+-----------------------------------------------+
| Keyboard shortcuts                       [✕] |
+-----------------------------------------------+
| [ N    ]  New thread                          |
| [ I    ]  Invite participant                  |
| [ A    ]  Archive thread                      |
| [ X    ]  Abandon thread                      |
| [ F    ]  Forward thread                      |
| [ R    ]  Focus composer                      |
| [ Ctrl+Enter ]  Send (in composer)            |
| [ Esc  ]  Close dialog                        |
| [ ?    ]  Show this help                      |
|                                               |
| Shortcuts are disabled while focus is inside  |
| an input or textarea.                         |
+-----------------------------------------------+
```

Future: when other features ship, this becomes a tabbed reference — one tab per feature — with a global "g i / g t / g k / g a / g g" jump table at the top.

---

## 6. Empty states (cross-screen)

Standard component (`empty_state` token), centered in its container, 28rem max-width, three layers:

1. A 32px icon (lineart, `text.muted` color). Reserve a small set: `inbox-empty`, `search-empty`, `disconnected`, `not-permitted`, `no-orgs`.
2. Title in `h3`, `text.secondary`.
3. Body in `body`, `text.muted`. May contain an inline KbdChip.
4. Optional primary CTA Button below.

Five canonical empty states:

| Where | Title | Body | CTA |
|---|---|---|---|
| Orgs list empty | "No orgs in this runtime." | "Initialize one with `happyranch orgs init <slug>`." | (none) |
| Inbox open, no threads | "No threads yet." | "Press `N` to compose, or have an agent dispatch one." | "+ New thread" |
| Inbox filter, no match | "No threads match the filter." | (none) | "Clear filter" (ghost) |
| Detail, none selected | "Select a thread from the inbox." | "Or press `N` to start a new one." | (none) |
| Detail, thread has no messages | "No messages yet." | "Waiting for the first reply." | (none) |

---

## 7. Error and disconnected states

### Daemon unreachable (`/api/v1/health` 4xx/5xx or fetch error)

The whole body of the app is replaced with an EmptyState variant:

```
                       ⚡
              Daemon unreachable
        Is the HappyRanch daemon running on this machine?

           [ Try again ]   [ Show CLI command ]
```

The CLI command popover shows: `scripts/daemon.sh start`. The TopBar stays rendered (so the founder can see what they were on); the Statusbar dot turns red. Polling continues every 5s — the body restores when health flips back.

### 401 (token rejected)

Bootstrap flow re-runs once. If still 401: same shell as "Daemon unreachable" but with title "Auth bootstrap failed" and body "The daemon refused this browser's bearer token. Restart the daemon, then click Try again."

### SSE dropped (in-thread)

No blocking UI. Statusbar dot turns yellow + the streams count decrements. The detail pane and inbox continue rendering cached data; the `useSSE` hook retries with the last `since_seq`. A toast appears only if reconnect fails 3 times in a row: "Real-time updates paused — click Refresh."

### Mutation error

Stays in the originating dialog or composer, with inline error in `tier.red`. A toast also pops with the same message — keeps the surface findable if the dialog is dismissed.

---

## 8. Tasks

### Purpose

Inbox + detail surface for every task across the org. Equivalent to `happyranch tasks list` + `happyranch details <task_id>` + `happyranch events <task_id>` + `happyranch cancel|revisit|resolve-escalation`.

### Layout

240px FilterSidebar + 1fr canvas. Detail pane mounts as a Drawer (480px slide-in from the right, `primitives/Drawer`) when `:task_id` is in the URL. Closing the Drawer (Esc or backdrop click) navigates back to `/orgs/:slug/tasks`.

### Inbox

Polled at 10s via TanStack Query (`refetchInterval: 10_000`). Filter groups: Status (pending / in_progress / blocked / completed / failed) and Team (auto-derived from the loaded task set). Rows are TaskCard patterns honoring `useDensity()`.

**Deferred:** Agent filter (umbrella §6.1) is not shipped in PR 7. `TaskRecord` has no `agent` field — agent is implied per task via orchestration events. A follow-up will either surface a derived `current_agent` column on the task row or filter via a separate event-derived index.

### Detail Drawer

- Header: TASK-NNN IdBadge, StatusBadge (extended to handle task statuses + `blockKind`), brief, team. Three action buttons — Resolve… (only when escalated), Revisit, Cancel.
- Recall tree: indented children, each row an IdBadge (deep-linked to that task's detail) + brief + status badge + output_summary if completed.
- Live events: SSE subscription to `/tasks/{id}/events`, appended chronologically. Terminal events (`task_complete` / `task_failed` / `task_blocked`) invalidate both the task detail and the inbox list.

### Dialogs

- CancelTaskDialog — required reason, destructive variant.
- RevisitTaskDialog — optional note, optional session-timeout override (positive integer). On success navigates to the new root.
- ResolveEscalationDialog — approve/reject radio + required rationale.

### Keyboard

- `g t` — jump to Tasks inbox (registered by TopBar via `useGlobalJump('t', ...)`).
- `Esc` — close Drawer or dialog.

### Data dependencies

- `GET /orgs/:slug/tasks` (polled).
- `GET /orgs/:slug/tasks/:id`, `GET /orgs/:slug/tasks/:id/recall` (one-shot per Drawer mount).
- `GET /orgs/:slug/tasks/:id/events` (SSE while Drawer is open).
- `POST /orgs/:slug/tasks/:id/cancel|revisit|resolve-escalation`.

### Drift from `2026-05-18-web-app-complete-feature-set-design.md`

The umbrella's §5.5 SSE budget assumed `/tasks/events` (inbox SSE) would land in PR 7. This PR uses polling instead. Net SSE streams added: 1 (per-task tail). If the inbox feels visibly stale in production, revisit the daemon-side event publish wiring as a follow-up; the cap of 4 concurrent streams is preserved either way.

---

## 9. KB — as built (PR 10)

### Purpose

Browse and read knowledge-base entries. Read-only by default. A flag-gated compose dialog lets the founder add entries without dropping to the CLI.

### Layout

- 240px left rail = search input + `FilterSidebar` (Types + Tags, derived from the unfiltered entry list).
- Canvas = `KbEntryCard` rows (slug, title, type, age, tags in comfortable density).
- Drawer detail at `/orgs/:slug/kb/:entry_slug` — markdown body via `<Markdown>`, source-task `IdBadge` linking to `/orgs/:slug/tasks/:task_id`, related-entry list.

### Filters

- **Type** = server-side `?type=` on `GET /kb`.
- **Tag** = client-side single-tag filter on the result set.
- **Search** = non-empty input switches the active query to `GET /kb/search?q=…`, debounced 200ms via a `setTimeout` ref. The active **type** filter is re-applied client-side over the search result set, since `/kb/search` ignores the `type` parameter.

### Compose (flag-gated)

`VITE_ENABLE_KB_COMPOSE=true` shows a `[Compose…]` button next to the page title. The dialog hardcodes `agent: "founder"` and calls `POST /kb`. Edits, deletes, and reindex stay CLI-only.

### Jump-key

`g k` from anywhere navigates to `/orgs/<active-slug>/kb` (registered in `TopBar`).

---

## 10. Audit — placeholder shell

### Purpose

The work log from `protocol/05e-dashboard.md` Page 3 (audit trail) plus Page 4 (escalation history) and Page 6 (execution traces). One feature folder, sub-routed.

### One-screen sketch

Three sub-tabs at the top of the canvas: **Activity** (filtered audit-log feed), **Escalations** (table), **Traces** (run tree). The shell is a `dashboard` grid: 240px filter sidebar + 1fr canvas, with the sub-tab bar pinned at the top of the canvas.

```
+-----------+----------------------------------------------+
| AGENT     |  Activity · Escalations · Traces             |
|  content… |                                              |
|  ops…     |  Apr 11 14:32  content_writer  wrote_content │
| TYPE      |    Task: Hong Kong visa guide                │
|  task     |    Confidence: 82                            │
|  kb       |  Apr 11 14:15  content_qa  reviewed_pass     │
| DATE      |    Task: Hong Kong visa guide                │
|  today    |    Verdict: PASS                             │
|  this wk… |  …                                           │
+-----------+----------------------------------------------+
```

Each row is an expandable item — clicking opens an inline panel with the full audit payload. The chronological list uses `body_sm` with the timestamp in `mono_sm`. AgentChips for the actor, `id_task` Badge for the linked task.

### Engineer note

Audit lists are dense — we lean on `density: compact` (32px row default). Trace tree on the Traces sub-tab uses indented bullets with cost annotations on the right edge.

---

## 12. Agents

### Purpose

Active roster of approved agents + the founder's pending-enrollment
review queue.

### Layout

Single canvas (no sidebar). Header carries the page title + a sub-tab bar:
**Active** (roster) and **Pending** (enrollment queue). Tab state rides
on a `?view=pending` query param rather than a static path segment —
agent names are arbitrary `[a-z][a-z0-9_]*`, so any static
`agents/<word>` sibling of `agents/:agent_name` would silently shadow a
real agent with that name. Clicking an agent row navigates to
`/orgs/:slug/agents/:agent_name`, which mounts the AgentDetailDrawer over
the (forced) Active tab.

### Active tab

Single roster table, honoring `useDensity()`:

```
+----------------------------------------------------------+
| Agent              Team       Executor   Description     |
| ● content_writer   content    claude     Drafts guides…  |
| ● content_qa       content    claude     Reviews drafts… |
| ● support_agent    cx         claude     Handles inquir… |
+----------------------------------------------------------+
```

Row anatomy: AgentChip with role dot, then team / executor / description
in `text.muted`. Cells with no underlying data render as `—`.

### Pending tab

Each enrollment renders as a card with name, team/executor metadata,
description, and two buttons:

- **Approve** — one-click POST `/agents/{name}/approve`.
- **Reject** — opens an inline dialog asking for an optional reason
  before POST `/agents/{name}/reject`.

The Approve / Reject mutations invalidate both the roster list and
the enrollments list so the post-action UI flips immediately.

### Detail Drawer

- Header: AgentChip + metadata line (team + executor) + optional
  description paragraph.
- Recent tasks: list of `TaskCard`s filtered by
  `?assigned_agent=<name>` on the tasks endpoint. Empty state when the
  agent has never been the assigned manager.
- Learnings: read-only list of summaries from
  `GET /agents/{name}/memory/entries/`. Surfaces a 412
  (`workspace_not_migrated`) error with a hint to run
  `happyranch memory reindex` rather than silently failing.
- Writes (creating a learning) stay agent-callback-only — the umbrella
  spec §5.7 reserves this surface for the CLI.

### Daemon contract

- `GET /agents` returns `name`, `team`, `role`, `executor`, `description`.
- `GET /agents/enrollments` returns `team`, `role`, `executor`, and
  `enrolled_by` so the Pending tab can render without a second
  roundtrip.
- `GET /tasks` accepts `?assigned_agent=<name>` so the Drawer can scope
  its task list.

### Keyboard

- `g g` — jump to the Agents page (registered by TopBar via
  `useGlobalJump('g', …)`).
- `Esc` — close the Drawer or the reject dialog.

---

## 13. Global UX — keyboard, toasts, theme, density

### Keyboard map (canonical)

| Key | Scope | Action |
|---|---|---|
| `g i` | global | Jump to `/orgs/:slug/threads` (inbox) |
| `g t` | global (future) | Jump to Tasks |
| `g k` | global (future) | Jump to KB |
| `g l` | global | (reserved — previously Jump to Talks) |
| `g a` | global (future) | Jump to Audit |
| `g g` | global (future) | Jump to Agents |
| `/` | active feature | Focus that feature's filter/search input |
| `N` | Threads | New thread |
| `I` | Threads detail | Invite |
| `A` | Threads detail | Archive |
| `X` | Threads detail | Abandon |
| `F` | Threads detail | Forward |
| `R` | Threads detail | Focus composer |
| `Ctrl+Enter` | Composer | Send |
| `Esc` | any dialog/drawer | Close |
| `?` | global | Help |

Shortcuts are suppressed when focus is inside an `<input>`, `<textarea>`, or `[contenteditable]`. Multi-key combos (`g i`) have a 1.0s buffer.

### Toast queue

Bottom-right docked stack, max 3 visible. Auto-dismiss after 6s; error toasts persist until clicked. Each toast has a colored `border-left` (info/success/warning/danger from `feedback.*`). No icons on toasts — the color and copy are enough.

### Theme toggle

In v0.1: dark is the only mode actually shipped. The toggle is rendered, hits a `useTheme()` hook that swaps a `data-theme="light|dark"` attribute on `<html>`, and `web/styles.css` ships both palettes already so a future PR can light up light mode by removing the dark-only override.

### Density toggle

Comfortable (default) ↔ Compact. Stored in `localStorage["happyranch.density"]`. Affects: InboxRow height, MessageList gap, audit-log row height. Does not affect message-body text size (we never make message bodies less legible).

