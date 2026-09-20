# HappyRanch web — architecture notes

Localhost React SPA bundled into the FastAPI daemon. See
`docs/superpowers/specs/2026-05-14-web-ui-design.md` for the full web-UI
design and `web/DESIGN_SYSTEM.md` for the design-system migration plan.

## Layers (strict)

1. **`src/lib/api/<X>.ts`** — Daemon route mirror. One TS module per
   `src/daemon/routes/<X>.py`. Pure functions over a shared `request()` helper.
   Returns typed objects. No React.
2. **`src/design-system/`** — Code-owned design system. Replaces the previous
   `src/components/` folder. Splits into:
   - **`tokens/tokens.css`** — single `@theme` block. Tailwind v4 derives all
     utilities from these CSS custom properties. It is the only raw-colour
     definition authority; the full production-tree scanner holds any legacy
     raw colour outside it to an exact, shrinking occurrence baseline.
   - **`primitives/`** — shadcn/ui-style components (Button, Dialog, …).
     Pure UI; allowed to import `@/lib/utils` only.
   - **`patterns/`** _(future)_ — composites of primitives (AgentChip,
     InboxRow, MessageBubble, …). Pure props in, JSX out.
   - **`layouts/`** _(future)_ — slot-based templates (AppShell, ThreadsLayout,
     DashboardLayout).
3. **`src/shared/<domain>/`** — Shared feature-safe modules. Hookful React
   components (dialogs, composers) that multiple feature folders need but
   that cannot live in `design-system/` (patterns are pure, no hooks) or
   `features/` (cross-feature imports forbidden). May import from
   `@/lib/`, `@/design-system/`, and `@/hooks/`. May NOT import from
   `@/features/`.
4. **`src/features/<domain>/`** — React feature folders. One folder per CLI
   domain. Owns pages, dialogs, and TanStack Query hooks. May import only from
   `@/lib/`, `@/design-system/`, `@/shared/`, and `@/hooks/`. **No
   cross-feature imports.**
5. **`src/lib/utils.ts`** — `cn` helper for class-name composition (used by
   primitives only).

## Boundary rule

> Every browser-callable daemon route maps 1:1 to one TS function in
> `src/lib/api/`. Features compose those functions through TanStack Query
> hooks. Features may not call `fetch` directly. Cross-feature imports are
> forbidden — share through `@/shared/`, `@/design-system/`, or `@/lib/`.
>
> `@/shared/` modules may import hooks, lib, and design-system but never
> `@/features/`. Primitives may not import patterns, layouts, hooks, or
> `@/lib/api`. Patterns may import primitives but not layouts.

## Internationalization (i18n)

Web copy uses the first-party typed contract in `src/lib/i18n/` (`locale`,
`catalog`, `format`, `coverage`) plus the `I18nProvider` in `src/hooks/i18n.tsx`
(`<html lang>`, `setLocale`, `t`/`render`). English (`en`) and Simplified
Chinese (`zh-CN`) catalogs are static and co-loaded; keys are typed, parameters
are named, and plurals declare explicit per-locale forms with a parity check.

The initial locale is resolved synchronously before the first React text
(`bootstrapDocumentLocale` in `src/main.tsx`) from the saved
`happyranch.ui.locale` value; production runs in preview mode, so an unset
preference renders English regardless of the environment. That one resolution
is handed through `App` → `AppShell` → `I18nProvider` (`initialResolution`) so
the provider never rereads a changed adapter snapshot. Storage failures degrade
to an in-memory session with an honest persistence result, and same-origin
`storage` events sync other tabs without write loops. Persistence
acknowledgements are sequenced, so a superseded write, an external change, or
unmount can never report a stale durable success.

Adapter ownership (`LocalePreferenceAdapter.authority`) decides who owns the
persisted choice: the browser adapter keeps `localStorage` authoritative and
consumes cross-tab events, while a `native` adapter's injected snapshot is
authoritative and browser values/events are ignored so a stale origin-scoped
value cannot override it.

`src/lib/format.ts` remains the canonical display formatter; the explicit-locale
interfaces in `src/lib/i18n/format.ts` delegate to it and do not fork a competing
implementation. Route/page translation is W2-W4 — not W1. The mount-time
coverage inventory lives in `src/lib/i18n/coverage.ts` and marks every
not-yet-migrated namespace English-only so fallback is never mistaken for
coverage. See `docs/superpowers/specs/2026-09-20-web-i18n-design.md`.

## What is intentionally not in here

- Agent-callback endpoints (`/report-completion`, `/manage-agent`,
  `/manage-repo`, `/dispatch`, `/learning add|update|promote`, thread
  `/reply`, `/decline`, `/dispatch`, `/close-out`). Those are agent-subprocess
  only and would be a privilege-escalation if exposed in the browser.
- `--as-founder` impersonation surface for KB deletes. Stays TTY-gated in CLI.
- Multi-user concerns: login screens, account model, RBAC. Localhost only.
