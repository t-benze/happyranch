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
implementation (`formatCount` takes an OPTIONAL locale so legacy callers keep
the host-default behaviour). An unexpected runtime gap renders the English
message with English grammar (`resolveMessage` reports the supplying catalog
locale), never a raw key. **W2a** translated the mounted shell (AppBar titles,
Sidebar nav/aria/org-switcher/account, root loading and NotFound, the
ErrorBoundary fallback copy, AddOrgDialog, and the shared help/palette
presentation) and added CJK-capable SYSTEM font fallbacks to the tokens.
**W2b** translated the onboarding route (`features/onboarding/OnboardingPage.tsx`,
`ConnectRuntimeStep.tsx`) and the shared `shared/connect/ConnectFlow.tsx`,
extracting the single `lib/addOrgError.ts` classifier consumed by both
AddOrgDialog and onboarding. Because ConnectFlow is shared, its Settings ▸
Executors mount is localized too. **W2c** translated the Settings surface
(`features/settings/SettingsPage.tsx` header/sub-nav/loading/error/panel copy and
the Assistant, Organization, Executors/custom-profiles/binaries and Daemon /
Capacity section bodies; raw daemon detail, identifiers, config keys and every
capacity number stay verbatim; the Work Hours-owned `EligibilityEditorDialog`
stays English until W4) and built the client-only Settings ▸ Preferences
language selector (`sections/PreferencesSection.tsx`). `SettingsPage` mounts the
`preferences` route OUTSIDE the `useSettings` loading/error/data gate, so it
works while the settings API is loading, failing or empty; the other panels
keep that gate. The selector is closed in production until W3:
`languagePreferenceGate.ts` mounts it only when the build sets
`VITE_ENABLE_I18N_PREFERENCES=true` (tests use `vi.stubEnv`; the W2c browser
harness builds a separate preview dist), ordinary builds tree-shake it out, and
a direct `/settings/preferences` URL falls to the existing Assistant redirect.
Route families (W3/W4) and the assistant dock body (W4) remain untranslated.
No public language selector is exposed and an unset preference still renders
English until W3. The mount-time coverage
inventory lives in `src/lib/i18n/coverage.ts`: it separates copy-bearing routes
(root shell `index`, the `*` NotFound catch-all and onboarding — now
`translated`) from
copy-free redirects, disambiguates colliding tokens with `<scope>:<token>`
qualified identities, and lists the ACTUAL mounted dialog components so
fallback is never mistaken for coverage. Foundation browser evidence runs the
isolated `src/design-system/i18n/I18nFoundation.stories.tsx` story and the real
`main.tsx` startup through `scripts/i18n-browser-evidence.mjs` (headless Chrome
over CDP, no new dependency). W2a shell evidence runs the same real startup
through `scripts/w2a-shell-browser-evidence.mjs` under an independent
`I18N_W2A_EVIDENCE` build gate, and W2b onboarding evidence drives the real
`/onboarding` route through `scripts/w2b-onboarding-browser-evidence.mjs`
against the ORDINARY build with NO evidence-only instrumentation — locale
switches use the supported `localStorage` preference + same-origin `storage`
event because there is no public selector. W2c Preferences evidence runs
`scripts/w2c-preferences-browser-evidence.mjs` against a preview dist built with
the explicit `VITE_ENABLE_I18N_PREFERENCES=true` activation and the ordinary
dist (which must redirect `/settings/preferences` and contain no Preferences
component); it drives the real radios with CDP input and records focus, node
identity, `<html lang>`, persistence status and a zero-request switch window,
plus causal negatives for a wrong locale, a remount and an API write. Every page installs and asserts the real
`navigator.language`/`navigator.languages` before app modules; the W1
production path is built with `I18N_BROWSER_EVIDENCE` so `vite.config.ts`
injects the test-only `src/test/i18n-evidence-consumer.tsx` next to
`<AppRoutes />`, and the W2a path injects
`src/test/w2a-shell-evidence-consumer.tsx` plus a test-only error trigger inside
the real boundary. The W2a first-commit record is
read from the ACTUAL committed
Sidebar/AppBar DOM (a layout-effect capture that is frozen on first connection
and never overwritten by a later locale correction), together with `<html lang>`
and the real navigator read-back; `I18N_W2A_EVIDENCE=negative` additionally
hands the provider a deliberately mismatched locale and corrects it afterwards,
so the same positive predicate must reject the first shell (causal negative
control). The W2b harness needs no consumer: it reads the onboarding DOM and
generated prompt directly. S4-S6 and S8-S10 retain actual focus, stable
test-only node identity, open phase/mode and raw values across both switch
directions; every immediately scoped switch window proves zero
settings/org/connect/mint mutations and zero `/api/` requests. A
backward-compatible optional `DialogContent.closeLabel` supplies the
localized accessible name for the built-in close control (defaulting to the
legacy English `Close`); AddOrgDialog, HelpSheet and CommandPalette pass it from
their callers and the patterns/primitives stay prop-driven with no locale hook
import. The palette's container keydown defers Enter to natively-activatable
descendants (`button`, `a[href]`, `[role="option"]`), so the localized X close
control closes on Enter without selecting a row while the search input still
selects the active row; the browser harness exercises this (S19: both locales,
populated and empty results) and covers the shell/help (S11/S16), AddOrg (S12
wide-light; S17 zh-narrow-light/en-narrow-dark/zh-wide-dark) and palette (S13
wide-light; S18 zh-narrow-light/en-narrow-dark/zh-wide-dark) states across the
observed 1440x900/390x844 light/dark combinations in both switch directions,
recording the actual `document.activeElement`, retained node identity, open state
and selection/value before and after each switch plus precisely scoped
switch-window request assertions (positive receipt 375/375 assertions, 30 PNGs).
Both env gates are no-ops for every ordinary build. See
`docs/superpowers/specs/2026-09-20-web-i18n-design.md`.

## What is intentionally not in here

- Agent-callback endpoints (`/report-completion`, `/manage-agent`,
  `/manage-repo`, `/dispatch`, `/learning add|update|promote`, thread
  `/reply`, `/decline`, `/dispatch`, `/close-out`). Those are agent-subprocess
  only and would be a privilege-escalation if exposed in the browser.
- `--as-founder` impersonation surface for KB deletes. Stays TTY-gated in CLI.
- Multi-user concerns: login screens, account model, RBAC. Localhost only.
