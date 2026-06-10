# Web UI — Design Spec

**Date:** 2026-05-14
**Status:** Draft, pending implementation plan.
**Replaces:** the Textual TUI at `src/tui/` (gated on web-UI parity for threads).
**Relates to:** `docs/superpowers/specs/2026-05-13-threads-design.md` (the first and only feature surfaced in this milestone).

## 1. Goal

Provide a browser-based founder console for OPC that replaces the Textual TUI for threads and is architected so future milestones can absorb every other CLI domain (tasks, KB, audit, agents, talks, tokens, orgs, runtime) without re-architecture.

The web UI is a single-page React + Tailwind application bundled into the existing FastAPI daemon. It calls the daemon's existing HTTP+SSE surface 1:1 with no new server-side aggregation. The CLI remains the source of truth for scripting and agent callbacks; the web UI is a founder-only surface.

## 2. Non-goals

- **Multi-user / remote access.** Localhost only. Single founder, single Mac Mini. No login screen, no account model, no row-level authorization.
- **Replacing the CLI.** All `opc ...` subcommands stay. The web UI does not aim for command parity in v1 beyond the threads feature.
- **Exposing agent-callback endpoints.** `report-completion`, `manage-agent`, `manage-repo`, `dispatch` (agent variant), `learning add/update/promote`, thread `/reply`, `/decline`, `/dispatch`, `/close-out` are agent-subprocess-only and stay invisible to the browser. The TS client omits them entirely so they cannot be misused.
- **`--as-founder` impersonation surface.** Founder-override of audited deletes stays TTY-gated through the CLI.
- **Mobile / responsive design.** Desktop founder workstation only.
- **Offline / PWA / service worker.**
- **Dark/light toggle.** Single dark palette in v1.
- **Internationalization layer.**
- **File/image uploads.** Markdown text bodies only, mirroring the thread data model.
- **Real-time token streaming of agent replies.** Replies arrive whole (matches threads spec §2).
- **Mid-feature E2E browser tests.** Add when a second feature lands.

## 3. Assumptions

1. Local single-user deployment on the founder's machine (Mac Mini today).
2. The daemon is the only backend. Web UI ↔ daemon is in-process from the user's perspective; same port, same origin in production.
3. The existing bearer token at `~/.opc/daemon.token` is the only credential. The browser obtains it via a localhost-gated bootstrap endpoint.
4. Threads feature parity with the current TUI is the gate for TUI removal. The two coexist until the founder signs off; then the TUI is deleted in the same PR.
5. Frontend code can live at the repo root alongside `src/`; the Python toolchain does not need to learn about Node.

## 4. Architecture

### 4.1 Process layout

One process. The FastAPI daemon serves both `/api/v1/*` (existing) and `/` (new SPA static mount). No separate Node service in production. In development, Vite runs on a second port and proxies `/api/*` to the daemon.

### 4.2 Directory layout (additions)

```
my-opc/
├── src/                      # Python — unchanged surface
│   └── daemon/
│       ├── app.py            # +mount StaticFiles for web/dist + SPA fallback
│       └── routes/
│           ├── auth.py       # NEW — GET /api/v1/auth/bootstrap (localhost-only)
│           └── web_static.py # NEW — SPA fallback handler (any non-/api/* path)
├── web/                      # NEW — all frontend lives here, isolated from Python
│   ├── package.json
│   ├── vite.config.ts        # /api proxy to daemon in dev (port read from ~/.opc/daemon.port)
│   ├── tailwind.config.ts
│   ├── postcss.config.cjs
│   ├── tsconfig.json
│   ├── index.html
│   ├── ARCHITECTURE.md       # codifies the boundary rule below
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── routes.tsx
│   │   ├── styles.css
│   │   ├── lib/
│   │   │   ├── api/
│   │   │   │   ├── client.ts     # request() helper, ApiError, base URL
│   │   │   │   ├── sse.ts        # auth-aware EventSource via @microsoft/fetch-event-source
│   │   │   │   ├── types.ts      # hand-mirrored from src/models.py
│   │   │   │   ├── orgs.ts
│   │   │   │   ├── runtime.ts
│   │   │   │   ├── tasks.ts
│   │   │   │   ├── agents.ts
│   │   │   │   ├── audit.ts
│   │   │   │   ├── tokens.ts
│   │   │   │   ├── kb.ts
│   │   │   │   ├── talks.ts
│   │   │   │   ├── threads.ts
│   │   │   │   └── index.ts
│   │   │   ├── auth.ts           # bootstrap fetch + sessionStorage
│   │   │   └── orgSlug.ts        # OrgProvider + active-slug hook
│   │   ├── components/           # generic primitives only — Button, Modal, Toast, DataTable
│   │   └── features/
│   │       └── threads/          # ONLY feature implemented in this milestone
│   │           ├── ThreadsPage.tsx
│   │           ├── ThreadDetailPane.tsx
│   │           ├── InboxList.tsx
│   │           ├── InboxRow.tsx
│   │           ├── MessageList.tsx
│   │           ├── MessageBubble.tsx
│   │           ├── Composer.tsx
│   │           ├── NewThreadDialog.tsx
│   │           ├── InviteDialog.tsx
│   │           ├── ArchiveDialog.tsx
│   │           ├── AbandonDialog.tsx
│   │           ├── KeyboardShortcuts.tsx
│   │           ├── HelpDrawer.tsx
│   │           ├── ThreadHeader.tsx
│   │           ├── hooks.ts
│   │           └── strings.ts    # error-code → human message map
│   ├── dist/                 # build output; gitignored; served by daemon if present
│   └── test/                 # MSW handlers, fixtures
└── docs/superpowers/specs/2026-05-14-web-ui-design.md
```

### 4.3 Three architectural layers

Layered strictly so adding a new feature later is a copy-paste of a known recipe.

1. **`lib/api/<X>.ts` — Daemon route mirror.** One TS module per `src/daemon/routes/<X>.py`. Pure functions over a shared `request()` helper. Returns typed objects. No React.
2. **`features/<domain>/` — React feature folder.** Pages, components, dialogs, and TanStack Query hooks for one CLI domain. May import only from `lib/` and `components/`. **Cross-feature imports are forbidden.**
3. **`components/` — Generic primitives.** Domain-agnostic UI atoms. Promoted from a feature only on third use.

This is codified in `web/ARCHITECTURE.md`:

> Every browser-callable daemon route maps 1:1 to one TS function in `lib/api/`. Features compose those functions through TanStack Query hooks. Features may not call `fetch` directly. Cross-feature imports are forbidden — share through `components/` or `lib/`.

### 4.4 Routing

`/orgs/:slug/<feature>` — mirrors `--org <slug>` in the CLI 1:1. An `OrgProvider` reads `:slug` from the route and exposes it via `useOrgSlug()`. The API layer takes slug as the first positional argument, mirroring the CLI signature. The active slug always lives in the URL, never in mutable global state — refresh-safe, shareable links.

Top-level shell renders a `TopBar` with an org picker (queries `GET /api/v1/orgs`) and a nav row. Only `Threads` is enabled in this milestone; future features render as disabled tabs with tooltips ("coming soon").

## 5. Frontend tech stack

| Concern | Choice | Rationale |
|---|---|---|
| Build | Vite | Fast dev server, native ESM, minimal config |
| Language | TypeScript (strict) | Mirrors the Pydantic type surface; catches the most common contract drift |
| UI | React 18 | Standard, well-understood, hooks fit our data model |
| Styling | Tailwind CSS | User-specified |
| Routing | React Router v6 | URL-as-state, nested layouts |
| Data fetching | TanStack Query v5 | Query cache, SSE-driven invalidation, mutations, stale-while-revalidate |
| SSE | `@microsoft/fetch-event-source` | Native `EventSource` cannot send `Authorization` headers |
| Markdown | `react-markdown` + `remark-gfm` | Render message bodies; no editor-side preview in v1 |
| Forms | React Hook Form | Lightweight, plays well with controlled inputs |
| Testing | Vitest + React Testing Library + MSW | Unit, component, and feature-integration in one runner |
| Component library | _None._ Build atoms inline; lift to `components/` on third use | Avoid premature dependency |

No `shadcn/ui`, no Radix, no Headless UI in v1 — every imported primitive is a future migration cost. We can add them on demand.

## 6. Data flow

### 6.1 Read path (queries)

Every list/detail view is a TanStack Query hook. Keys mirror the URL:

- `["threads", slug]` — inbox list
- `["thread", slug, threadId]` — single thread metadata
- `["thread-messages", slug, threadId]` — message list
- `["orgs"]` — top-level org picker

Default `staleTime: 30_000`. SSE invalidation overrides stale time when fresher data arrives.

### 6.2 Real-time path (SSE)

A single `useSSE(url, { onEvent, sinceParam })` hook wraps `@microsoft/fetch-event-source`. Two streams active in the threads feature, both already exposed by the daemon:

| Stream | When mounted | Action on event |
|---|---|---|
| `GET /threads/events` | Threads page | Invalidate `["threads", slug]` |
| `GET /threads/{id}/tail?since_seq=N` | Thread detail pane | Append to `["thread-messages", ...]` cache, advance `since_seq` |

On reconnect after drop: hook re-issues with the last seen `since_seq`. Matches the contract the TUI already uses (`95850db`, `0e593f7`).

### 6.3 Write path (mutations)

Each user action is one `useMutation`. On success, the mutation invalidates exactly the affected query keys — no global refetch. Optimistic updates are deliberately off in v1; the daemon's SSE emission delivers the canonical row a moment later.

Errors propagate as `ApiError` with `.code`, `.status`, and the raw `detail` payload. A `strings.ts` map per feature renders known codes as human messages; unknown codes fall back to the raw payload in a toast.

### 6.4 Auth flow

1. SPA loads. `lib/auth.ts` checks `sessionStorage["opc.token"]`.
2. If missing, fetch `GET /api/v1/auth/bootstrap`. Daemon returns `{token}` from `~/.opc/daemon.token` only when `request.client.host in {"127.0.0.1", "::1"}`. Other hosts → 403. **No reverse-proxy assumption:** the daemon is the terminal hop in the deployment model, so `request.client.host` is the real peer; we do not honor `X-Forwarded-For`. If a future deployment introduces a proxy, this endpoint must be reworked before exposing the proxy publicly.
3. Token stored in `sessionStorage`, attached as `Authorization: Bearer …` to every subsequent request and SSE stream.
4. `401` from any subsequent call clears the cached token and re-bootstraps once. If that fails, render a "daemon down or unauthorized" error screen.

The existing bearer model for CLI + agent callbacks is unchanged. Tokens never appear in URLs or logs.

## 7. Threads feature (v1 scope)

### 7.1 Routes

- `/orgs/:slug/threads` — inbox + (optionally) detail pane
- `/orgs/:slug/threads/:thread_id` — same layout, detail pane populated

### 7.2 Layout

Two-pane grid. Top bar with org dropdown + feature tabs. Left pane: inbox. Right pane: thread detail (header + messages + composer). Floating action overlay for keyboard shortcuts: `N`/`I`/`A`/`X`/`F`/`R`/`?`/`Ctrl+Enter`/`g i`/`/`.

### 7.3 Components

| Component | Role |
|---|---|
| `ThreadsPage` | Owns inbox SSE, renders shell |
| `InboxList` | Virtualized list, status filter chips, client-side text filter |
| `InboxRow` | Subject, last speaker, preview, age, "needs you" badge if last `addressed_to` includes founder |
| `ThreadDetailPane` | Header + messages + composer for selected thread |
| `ThreadHeader` | Subject, status, participants chips (tier-colored), turns used/cap, archive summary if archived |
| `MessageList` | Virtualized rows |
| `MessageBubble` | Speaker chip, timestamp, `addressed_to` chips, markdown body; `kind='decline'` in red; `kind='system'` as a slim event row |
| `Composer` | Markdown textarea + `To:` chip-picker (default `@all`, picker constrained to current participants), Send button, Ctrl+Enter |
| `NewThreadDialog` | Subject, recipients (autocomplete from `GET /agents`), body; reused for "Forward" with prefilled quoted excerpt |
| `InviteDialog` | Agent autocomplete |
| `ArchiveDialog` | Summary textarea + "request close-outs" checkbox (default on) |
| `AbandonDialog` | Reason input |
| `KeyboardShortcuts` | Headless component binding keys |
| `HelpDrawer` | `?` opens cheat sheet (mirrors TUI HelpScreen) |

### 7.4 Hooks

```ts
useThreadsList(slug, { status })
useThread(slug, threadId)
useThreadMessages(slug, threadId)
useThreadsInboxSSE(slug)
useThreadTailSSE(slug, threadId, sinceSeq)
useComposeThread(slug)               // POST /threads
useSendReply(slug, threadId)         // POST /threads/{id}/send  (founder reply path)
useInviteAgent(slug, threadId)       // POST /threads/{id}/invite
useArchiveThread(slug, threadId)     // POST /threads/{id}/archive
useAbandonThread(slug, threadId)     // POST /threads/{id}/abandon
useExtendTurnCap(slug, threadId)     // POST /threads/{id}/extend
```

Endpoints **not** exposed in the TS client: `/reply`, `/decline`, `/dispatch`, `/close-out` (all require agent invocation tokens minted only to subprocess agents).

### 7.5 Empty states

1. No threads yet — "Press N to compose."
2. No thread selected — "Select a thread from the list."
3. Daemon offline — "Daemon unreachable" with retry.

### 7.6 Keyboard parity

Bindings match the TUI 1:1 so muscle memory transfers: `N` new, `I` invite, `A` archive, `X` abandon, `F` forward, `R` focus composer, `Ctrl+Enter` send, `?` help, `g i` jump to inbox, `/` filter. The TUI's modal Esc-to-dismiss convention is preserved.

## 8. Extensibility recipe (future features)

When a new domain (e.g., `tasks`) gets surfaced in the web UI:

1. **Mirror the route file.** Create `web/src/lib/api/<X>.ts` with one exported function per `@router.*` decorator in `src/daemon/routes/<X>.py`.
2. **Add types.** Mirror the relevant Pydantic models in `lib/api/types.ts`. The contract test (§9) catches drift.
3. **Add the feature folder.** `features/<domain>/<Domain>Page.tsx`, `hooks.ts`, dialogs, components — same internal shape as `features/threads/`.
4. **Register routes.** One line per route in `web/src/routes.tsx`.
5. **Enable the nav entry.** One line in `TopBar.tsx`.

A new domain touches one folder plus two one-line registrations. No scaffolding work bleeds across.

**Pre-built in v1 even though no UI consumes it:** `lib/api/` modules for `orgs`, `runtime`, `tasks`, `agents`, `audit`, `tokens`, `kb`, `talks`, `threads`. ~400 LOC of mechanical wrappers. Reasons: forces the conventions to generalize before they calcify, and makes the contract test meaningful from day one. **Excluded** from this layer: every agent-callback endpoint (see §2).

## 9. Testing

| Layer | Tool | Scope |
|---|---|---|
| TS units | Vitest | `request()` helper, SSE reconnect/`since_seq` handling, error-code mapping, URL/slug helpers |
| Component | Vitest + React Testing Library | Each dialog's validation and submit payload; `Composer` shortcuts; `MessageList` rendering of all three `kind` values; empty states |
| Feature integration | Vitest + MSW | Threads page wired to mocked daemon: compose → see in inbox; reply → see in tail; archive → status changes. MSW handlers live in `web/src/test/handlers.ts` |
| Contract (Python) | pytest | Snapshot of `app.openapi()` JSON checked into `tests/contract/openapi.json`. Test fails on schema drift |
| Contract (TS) | Vitest | Reads the same `openapi.json` and asserts every documented route has a TS function in `lib/api/` |
| E2E browser | _Deferred_ | Add Playwright when a second feature lands |

`web/` gets its own `npm test`. The Python `pytest` suite is unchanged.

## 10. Ops & lifecycle

- **Build artifact.** `web/dist/` is **gitignored**. `scripts/build_web.sh` runs `npm ci && npm run build`. If the daemon's `web/dist/` is missing, the `/` route returns a small HTML page with the build command.
- **CLI integration.** New command `opc web [--no-open] [--port N]`. Checks `/health`, prints `http://127.0.0.1:<port>/`, opens default browser. No new daemon flags; UI mounts on the daemon's existing port.
- **Daemon dependency.** `pyproject.toml` is untouched. The Python side adds only `StaticFiles` (already a FastAPI primitive) and two small route modules (`auth.py`, `web_static.py`).
- **Dev hot reload.** `cd web && npm run dev` starts Vite on :5173 with `/api` proxied to the daemon port from `~/.opc/daemon.port`. The daemon does not know Vite exists.

## 11. Documentation updates

- **README.md** — new "Web UI" subsection under "Running the Daemon + CLI": build command, `opc web` launch, single-user/localhost note.
- **CLAUDE.md** — new top-level "Web UI" subsection mirroring §4 (directory layout, three-layer architecture, boundary rule) and a Tech-Stack row pointing at `web/`.

## 12. Removals (deferred to the same milestone)

Gated on founder sign-off that the web app reaches threads parity.

- Delete `src/tui/` (~750 LOC, 3 files).
- Update `cmd_threads_tui` in `src/cli.py`: no-subcommand `opc threads` prints `opc threads — please use \`opc web\`` and exits 0. All `opc threads <verb>` subcommands (`compose`, `list`, `show`, `reply`, `decline`, `dispatch`, `close-out`, `send`) stay untouched — scripts and agent callbacks depend on them.
- Remove `textual` from `pyproject.toml` if it has no other consumer.
- Update README + CLAUDE.md threads references to point at `opc web`.

The TUI removal lands in the same PR as the web app reaching parity. Until that PR is merged, both surfaces work.

## 13. Open questions resolved during design

- **Should we coexist with the TUI permanently?** No. Removed once parity is signed off; CLI subcommands stay.
- **Should we auto-generate TS types from Pydantic?** Not in v1. The generators we'd need are immature for our `StrEnum` + JSON-column patterns. Hand-mirrored types + an OpenAPI snapshot contract test catch drift more reliably for our size.
- **Should the web app live under `src/web/`?** No. Python and JS tooling do not mix cleanly under a shared root. `web/` at repo root keeps each toolchain self-contained.
- **Bundle `web/dist/` into git?** No. Gitignored; built on demand. Keeps the repo lean and avoids merge churn on minified bundles.
- **Auth UX for the browser?** Localhost-only bootstrap endpoint returns the existing token. No login screen, no separate credential.

## 14. Implementation order

1. `web/` scaffolding: Vite + TS + Tailwind + Router + Query + ESLint + Vitest.
2. Daemon: `auth.py` (`/api/v1/auth/bootstrap`) + `web_static.py` + static mount in `app.py`.
3. `lib/api/` modules for **every** existing route file (mechanical mirror).
4. Contract tests (Python OpenAPI snapshot + TS counterpart check).
5. `features/threads/` UI build-out.
6. `opc web` CLI command + `scripts/build_web.sh`.
7. Sign-off → delete TUI, update docs.

Each step has independent verification (lint, unit, contract, manual). Detailed sequencing belongs in the implementation plan, not this spec.

## 15. Assets surface (delivered 2026-06-10, scope A)

Per founder decision THR-007, the org-shared artifact store (the `happyranch assets put|list|get` CLI surface) gets a founder-facing web page. **Round one is read + create only** — the daemon (`runtime/daemon/routes/artifacts.py`) exposes exactly three routes and intentionally has **no delete and no update**, so the UI exposes none either.

- **Route.** `/orgs/:slug/assets` → `features/assets/AssetsPage.tsx`, registered in `routes.tsx` alongside the other `/orgs/:slug` feature pages. An `Assets` tab is added to the `TopBar` nav using the standard slug-guarded `placeholderTab` pattern.
- **CRUD coverage.**
  - **Create (upload)** — `POST /api/v1/orgs/{slug}/artifacts` via the existing `uploadArtifact()`, attributed `agent: 'founder'`.
  - **Read (list)** — `GET /api/v1/orgs/{slug}/artifacts` via a new thin `listArtifacts(slug)` client wrapper (mirrors `uploadArtifact()`'s bearer-token + 401-retry shape; reuses the existing error parser).
  - **Read (download)** — `GET /api/v1/orgs/{slug}/artifacts/{name}` via a plain `<a href>` built from the existing `artifactDownloadPath()`.
  - **Update / Delete** — **not present.** No daemon route exists; out of scope and backend-gated.
- **Client-side validation.** The upload form rejects, before calling the API, anything that would 400/413 on the daemon: per-file size cap of 10 MB and a name matching `^[A-Za-z0-9._-]+$` of at most 200 characters (`features/assets/validation.ts`). Violations surface as an inline error.
- **Non-goal update.** This supersedes the v1 "no file uploads" non-goal (§2) for the assets surface specifically, consistent with the thread-attachment upload path already shipped. Markdown-only bodies remain the rule for thread/talk message text.
- **Tests.** Validation unit tests (oversize / bad-char / >200-char / happy path), a `listArtifacts()` wiring unit test (happy path + 401-retry), and an MSW feature-integration test (list renders with download links; invalid name blocks the POST client-side).
