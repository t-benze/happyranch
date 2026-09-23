# Web i18n — foundation + W2a shell contract

> Status: current (W1 foundation + W2a mounted-shell migration)
> Current Source: `web/src/lib/i18n/`, `web/src/hooks/i18n.tsx`, the mounted
> shell consumers, this spec.
> Supersedes: the `Internationalization layer` non-goal in
> `2026-05-14-web-ui-design.md` (historical text preserved with a supersession
> annotation).
> Notes: W1 shipped the foundation; **W2a** translated the mounted shell
> (AppBar/Sidebar/root/not-found/ErrorBoundary/AddOrgDialog/help/palette).
> Later slices (W2b onboarding, W2c Settings, W3 route families, W4 remaining
> surfaces) and native preference persistence (N1) are still open. The W1
> sections below are retained as the historical W1 contract and updated per
> phase.

## 1. Scope

THR-118 implements a first-party, typed English (`en`) / Simplified Chinese
(`zh-CN`) contract for the web console. **W1** covered the foundation,
contract and test harness; it intentionally did not translate any route or
page, ship a public language selector, enable the preview selector, or touch
the native app.

**W2a** (this phase) translates the already-mounted shell presentation into
both locales while retaining the existing layout and interactions: the AppBar
page titles and controls, Sidebar navigation/aria/org-switcher/account copy,
the root loading and not-found fallback, the ErrorBoundary fallback copy, the
AddOrgDialog, and the shared help/palette presentation strings. It adds
CJK-capable SYSTEM font fallbacks to the design tokens (no webfont download or
dependency) and marks the migrated namespaces `translated` in the coverage
manifest. It does **not** translate onboarding (W2b), Settings/Preferences
(W2c), the assistant dock body (W4) or any route family (W3/W4); it adds no
public language selector and keeps an unset preference on English until W3.

Delivery status at W1 (keep separate from later phases):

| Area | W1 status |
| --- | --- |
| Typed `en`/`zh-CN` catalog, named params, explicit plurals, parity check | shipped |
| Synchronous initial resolution + `<html lang>` | shipped |
| Browser preference (`happyranch.ui.locale`) with resilient storage | shipped |
| Injectable locale preference adapter (native seam) | shipped (browser + test doubles only) |
| Mounted-route/namespace coverage manifest | shipped |
| Foundation browser/Storybook evidence (isolated, non-product) | shipped — `web/scripts/i18n-browser-evidence.mjs` + `I18nFoundation.stories.tsx` + an `I18N_BROWSER_EVIDENCE`-gated test-only first-commit consumer |
| Explicit-locale display formatters | shipped (interfaces only; no caller migration) |
| Mounted shell translation (W2a: AppBar/Sidebar/root/not-found/ErrorBoundary/AddOrgDialog/help/palette) | **shipped — W2a** |
| CJK-capable system font fallbacks (no download) | **shipped — W2a** |
| Route/page translation | **W2b/W2c/W3/W4** — not shipped |
| Public language selector / opt-in preview | **W3** — not shipped |
| Full-mode automatic environment detection | implemented + unit-tested, **not enabled** in production (W5) |
| Native preference persistence (Swift/message handler) | **N0/N1** — not shipped |

## 2. Exports

| Module | Contract |
| --- | --- |
| `@/lib/i18n/locale` | `Locale`, `LocaleMode`, `LocaleSource`, `LocaleAuthority`, `LOCALE_STORAGE_KEY`, `SUPPORTED_LOCALES`, `isLocale`, `matchSystemLocale`, `resolveLocale`, `LocalePreferenceAdapter`, `LocaleSnapshot`, `LocaleWriteOutcome`, `browserLocalePreferenceAdapter`, `adapterAuthority`, `readAdapterSnapshot`, `getLocalStorage`, `applyDocumentLocale`, `bootstrapDocumentLocale` |
| `@/lib/i18n/catalog` | `MessageKey`, `MessageValue`, `PluralForms`, `Catalog`, `catalogs`, `PLURAL_FORMS_BY_LOCALE`, `translate`, `renderTranslated`, `lookupMessage`, `resolveMessage`, `selectPluralCategory`, `interpolate`, `extractPlaceholders`, `validateCatalogParity`, `assertCatalogParity` |
| `@/lib/i18n/format` | `formatTokensFor`, `formatCountFor`, `formatDateTimeFor` |
| `@/lib/i18n/coverage` | `COVERAGE_MANIFEST`, `NOT_APPLICABLE_NAMESPACES`, `classifyRouteToken`, `classifyRouteIdentity`, `unclassifiedRouteTokens`, `unclassifiedRouteIdentities`, `coverageSummary`, `describeCoverage`, `extractRouteTokens` |
| `@/hooks/i18n` | `I18nProvider` (`adapter`, `mode`, `initialResolution`), `useI18n`, `useLocale`, `useTranslation` |
| `@/App` | `App`, `AppShell` (production provider/route composition) |

## 3. Resolution and precedence

One synchronous resolver (`resolveLocale`) decides the locale. The `authority`
input names which layer owns the stored choice:

- **`browser` (default)** order:
  1. a valid **saved** explicit browser choice,
  2. a valid **native** value injected by the host,
  3. the **system/browser** language list — **full mode only**,
  4. **English**.
- **`native`** order: the injected **native** snapshot first, then the
  system/browser list (full mode only), then **English**. The browser
  `saved` value is never consulted, so a stale `happyranch.ui.locale` left by an
  earlier app origin/port cannot override the authoritative native preference.

Rules:

- In browser authority a saved valid explicit choice always wins; in native
  authority the native snapshot always wins.
- Any `zh*` tag maps to `zh-CN`; `en*` maps to `en`; the first recognized tag
  wins in full mode; unknown tags are skipped.
- **Production stays in preview mode**: there is no auto-detection, so an unset
  preference renders English even with a Chinese environment.
- Full-mode resolution exists and is unit-tested for W5 but is not enabled by
  the W1 production wiring.
- `bootstrapDocumentLocale()` is called from `web/src/main.tsx` before the first
  React text, applies `<html lang>`, and returns the one resolution; `main.tsx`
  hands it to `<App initialLocale>` → `<AppShell>` → `<I18nProvider
  initialResolution>`, so the provider never resolves a second, possibly
  different, adapter snapshot.

## 4. Persistence, failures and tab sync

- Browser key: `happyranch.ui.locale`.
- Storage reads/writes are wrapped: an unavailable, throwing or quota-limited
  store degrades to an in-memory session instead of crashing. `setLocale`
  applies immediately; the UI stays switchable.
- Persistence is reported honestly: a durable write is acknowledged only when
  the adapter says so; a delayed acknowledgment surfaces as `pending`, then
  `durable` or `failed`. A `localStorage` write is never treated as native
  success.
- Acknowledgements are sequenced. Two quick choices settle independently: the
  latest choice and its honest persistence state always win, and a superseded
  write's late success/failure is discarded. An external storage change or
  component teardown invalidates an in-flight write, so a stale completion can
  never report durable success for a value the session no longer holds.
- Same-origin `storage` events synchronize external `en`/`zh-CN` changes,
  invalid values, key deletion and `clear()`. The provider never writes back on
  a storage event (no loops) and removes its listener on unmount (StrictMode
  safe). The handler resolves `localStorage` through a guarded accessor: a
  throwing getter cannot crash the handler, and an area it cannot verify is
  ignored rather than guessed.
- Adapter ownership decides whether browser events apply at all: the browser
  adapter consumes them; a `native` adapter's injected snapshot is authoritative
  and browser events are ignored, so a stale origin-scoped value cannot override
  the native preference. Browser cross-tab updates are unchanged.
- Changing the locale never keys or remounts the tree: copy changes while
  drafts, selections, open dialogs and component identity are preserved.

## 5. Catalog contract

- `MessageKey` is the literal key union derived from the English catalog; the
  Chinese catalog is typed `Record<MessageKey, MessageValue>`, so a missing key
  is a compile-time error.
- Placeholders are named (`{name}`); translated slots may move freely.
- Plurals declare explicit per-locale forms: English requires `one` + `other`,
  Chinese requires `other`. `validateCatalogParity` rejects a missing/extra key,
  a shape mismatch, a missing/unexpected plural form and any placeholder-set
  drift.
- `translate` returns a plain string; `renderTranslated` returns escaped
  text/React nodes. There is no `dangerouslySetInnerHTML` path.
- An unexpected runtime gap falls back to English; a key absent from every
  catalog renders nothing — never a raw key. `resolveMessage` reports the locale
  whose catalog actually supplied the value, and plural selection uses that
  locale, so a missing Chinese entry renders the English grammar (`1 item`,
  never `1 items`) in both `translate` and `renderTranslated`.

## 6. Coverage manifest

`COVERAGE_MANIFEST` classifies every mounted route namespace as `translated`,
`english-only`, or `not-applicable`, and records reachable shared dialogs as
`surfaces`. `coverage.test.ts` scans `routes.tsx`, `prototypes/index.tsx` and
`SettingsPage.tsx` for `path="..."` tokens (plus the literal `index` route
token) and fails when a newly mounted route token is unclassified.

Copy-bearing and copy-free routes are separated: the root-shell `index`
(`RootRedirect` loading copy) and the app catch-all `*` (`NotFound`) are
`translated` in W2a; the copy-free `NavigateToHome`/`SpendRedirect`/
`ScheduleRedirect` and the settings-internal redirects are `not-applicable`.
Tokens that collide across modules are disambiguated with `<scope>:<token>`
qualified identities (`routes.tsx:*` vs `SettingsPage.tsx:*`,
`SettingsPage.tsx:index`, `SettingsPage.tsx:agents`).

Dialog/overlay surfaces are the ACTUAL mounted component names, and the test
anchors them to the real consumer sources (e.g. `ThreadsPage.tsx` mounts
`NewThreadDialog`/`InviteDialog`/`ArchiveDialog`/`RemoveParticipantDialog`;
`TaskDetailPage.tsx` mounts `CancelTaskDialog`/`RevisitTaskDialog`/
`ResolveEscalationDialog`; `JobDetailPage.tsx` mounts
`RunJobDialog`/`RejectJobDialog`), so an invented or unreachable name fails the
test. In W2a exactly four namespaces are `translated` (`root-shell`,
`not-found`, `app-shell`, `help-and-palette`); every later slice and route
family is still visibly `english-only`, so English fallback is never mistaken
for coverage. The `system-assistant` namespace now records only
`AssistantDockHost` (the W4 dock body); the help/palette hosts moved to the
`help-and-palette` namespace.

## 7. Formatting contract

- `@/lib/format` remains the canonical formatter. `formatTokensFor('en', …)` and
  `formatCountFor('en', …)` delegate to `formatTokens`/`formatCount`; there is
  no competing implementation. `formatCount` gained an OPTIONAL explicit locale
  argument (`formatCount(n, locale?)`): omitting it keeps the legacy
  host-locale behaviour for every existing caller byte-for-byte, while passing
  it makes the grouped output independent of the host `Intl` default (explicit
  English renders `1,234,567` even under `LC_ALL=de_DE.UTF-8`).
- Chinese compact display uses 万 / 亿; exact counts stay grouped and exact
  (`1,000` is never compacted).
- `formatDateTimeFor` requires an explicit locale and IANA timezone and is
  deterministic across hosts.
- Exact identifiers/config values/machine inputs keep their literal form;
  schedule-timezone calculations are untouched. Broad caller migration is
  W2/W4.

## 8. Adapter seam (native, W5/N1)

`LocalePreferenceAdapter` is the narrow injectable contract:

```ts
interface LocalePreferenceAdapter {
  readonly id: string;
  readonly authority?: 'browser' | 'native'; // default 'browser'
  readSnapshot(): LocaleSnapshot;      // saved / native / systemLanguages
  write(locale: Locale): LocaleWriteResult; // sync or Promise acknowledgement
}
```

`authority` is the ownership declaration. A `native` adapter makes the injected
snapshot authoritative (browser `saved` values and storage events do not apply);
a `browser` adapter keeps localStorage authoritative and consumes cross-tab
events. W1 ships only the browser implementation and test doubles. A fake
synchronous native initial value must render the translated probe on the first
pass with no English flash; a native snapshot plus a conflicting browser event
must preserve native authority; a delayed or rejected acknowledgement must never
be reported as a durable success. No Swift edits, message-handler installation,
credential access or native security-boundary change is in W1.

## 9. Verification

Focused Vitest tests (Testing Library + MSW) cover the W1 acceptance matrix:
precedence, storage failures, tab sync, hydration/state preservation, catalog
parity and safe rendering, formatting, the coverage manifest, and the adapter
seam. `scripts/local_ci.sh web` runs lint, typecheck, build,
`build-storybook` and `vitest run` under Node 24.

Foundational browser evidence is provided by
`web/scripts/i18n-browser-evidence.mjs` (no new dependency: Node 24's built-in
WebSocket + the installed headless Chrome) against two same-origin servers:
the built Storybook foundation story
(`src/design-system/i18n/I18nFoundation.stories.tsx`) and the production SPA
bundle (`web/dist`) with a synthetic `/api/v1` stub. Every page installs and
then asserts the REAL `navigator.language`/`navigator.languages` input at
document start, before app modules, and the receipt records the actual values —
`Emulation.setLocaleOverride` alone changes only `Intl` and was insufficient
(the shipping resolver reads `navigator`). A setup mismatch is a failing
assertion, never a silent fallback.

For the real `main.tsx → App → createBrowserRouter → AppShell → I18nProvider`
startup the harness builds with `I18N_BROWSER_EVIDENCE=1`, which makes
`vite.config.ts` inject the test-only `src/test/i18n-evidence-consumer.tsx`
adjacent to `<AppRoutes />`. That consumer renders a real catalog key
(`common.translatedProbe`) and freezes its first-render text plus `<html lang>`
before any layout/passive effect, so the assertions observe the FIRST COMMITTED
consumer text, not an eventual snapshot. The resolver, bootstrap snapshot
handoff, provider and router are never rewritten, and the transform returns
`null` for every ordinary build. Asserted cases: saved `zh-CN` in an English
environment renders the Chinese catalog string with `html.lang=zh-CN` at the
same first commit; saved `en` under an asserted Chinese environment renders
English with `html.lang=en`; unset preview under an asserted Chinese environment
renders English with `html.lang=en` (W5 detection stays disabled). An isolated
`I18N_BROWSER_EVIDENCE=negative` build feeds a deliberately mismatched provider
locale while the document locale stays correct and repairs it from a passive
effect; its genuine first-commit assertion fails as designed (recorded expected
failing exit), proving the check is causal.

It also asserts saved explicit English under a Chinese environment, saved
`zh-CN`, unset preview English, state preservation across a locale switch,
fail-safe storage read/write failure, real same-origin tab
change/delete/clear with no echo write, and that Chinese copy is rendered by a
CJK platform font (captured PNGs + `receipt.json` bound to the head SHA under
the task evidence directory). This is W1 foundation evidence only: it does not
claim translated product routes.

**W2a shell browser evidence.** `web/scripts/w2a-shell-browser-evidence.mjs`
drives the real built SPA bundle under
`I18N_W2A_EVIDENCE=1` (an independent evidence-only Vite transform that injects
the test-only `src/test/w2a-shell-evidence-consumer.tsx` next to `<AppRoutes />`
and a test-only error trigger inside the real `AppShellErrorBoundary`; it is a
no-op for every ordinary build). The consumer reads the **actual first committed
Sidebar/AppBar output from the real DOM** in a layout effect, freezes that
record on first connection, and never overwrites it when a later effect corrects
the locale; it records `html.lang` and the real
`navigator.language`/`navigator.languages` read-back at the same instant.
Against a synthetic `/api/v1` stub it asserts the frozen first shell (saved
`zh-CN` → Chinese/`zh-CN`; saved `en` under a Chinese navigator → English/`en`;
unset preview under a Chinese navigator → English/`en`), then exercises the
mounted shell in both locales: root loading copy, populated org navigation,
no-org, NotFound, the help drawer with a non-default tab, the AddOrgDialog with a
typed slug and a mapped error, the ErrorBoundary fallback with the raw stack
preserved and the localized Retry actually recovering, and the palette with a
query matching multiple rows and a non-default selected row. The shell and the
help/AddOrg/error/palette states run across 1440×900 and 390×844, light and dark
(including the Chinese wide-dark shell and a representative 390×844 dark
ErrorBoundary). Locale switching in the focus/identity cases is driven through a
real `storage` event, not the test control, so the control never steals focus;
for the help non-default tab (S16), the AddOrg typed slug + mapped error
(wide-light S12; the zh-narrow-light/en-narrow-dark/zh-wide-dark matrix S17) and
the palette query + non-default selected row (wide-light S13; the
zh-narrow-light/en-narrow-dark/zh-wide-dark matrix S18) the harness records
`document.activeElement`, retained `data-hrIdentity` node identity, open state
and the selected/value state BEFORE and AFTER EACH switch direction (an
en→zh-CN→en or zh-CN→en→zh-CN sequence). It also exercises the palette's actual
localized X close control (S19) in both locales with populated and empty result
sets: after the settled initial combobox focus it focuses the X by its exact
accessible name (`Close`/`关闭`), reads the focus back and dispatches native
Enter/Space/Escape, asserting the palette closes once with zero selection and an
unchanged pathname after EACH key, while a search-input Enter and an ArrowDown +
Enter on a non-default row still select exactly once. Switch-window requests are
scoped precisely to the measured endpoints: each help/AddOrg switch window
asserts no `PUT /settings/org` and no `POST /api/v1/orgs`, and each palette
switch window additionally asserts zero `/api/` requests (cache-only), per
direction. `I18N_W2A_EVIDENCE=negative` additionally hands
the provider a deliberately mismatched locale and installs the passive
correction component, so the real shell commits the wrong language first and
repairs itself; the harness's SAME positive predicate must reject that first
shell (recorded expected failing exit 1; exit 2 means the fixture itself never
mismatched). `CSS.getPlatformFontsForNode` proves the Chinese nav glyphs use a
CJK-capable platform font, and geometry assertions prove no document horizontal
overflow, no nav link outside the viewport, and dialogs contained within
1440x900 and 390x844. The state matrix covers the shell and the help (S11/S16),
AddOrg (S12/S17), palette (S13/S18) and error/dormant-palette states in both
locales across the observed 1440x900/390x844 light/dark combinations; it does
not claim every state at every viewport/theme. PNGs and `receipt.json` are bound
to the head SHA (the current positive receipt is 375/375 assertions and 30 PNGs;
exact counts are bound to the pushed `receipt.json`). The harness
cannot open the dormant command palette through the shipping hotkey (which stays
retired and is asserted not to open it); the palette is exercised through the
pattern-level probe, and its host/section localization is covered by focused
Vitest.

Frontend readiness map (actual evidence):

| Readiness item | W1 | W2a |
| --- | --- | --- |
| Route-wide Chinese rendering | N/A — no route translated in W1 (manifest marks every namespace `english-only`) | mounted-shell only; W2b/W2c/W3/W4 routes still `english-only` |
| Public language selector | N/A — W3 | N/A — W3 (still absent) |
| Browser two-tab live evidence | covered by unit/integration storage-event tests AND captured same-origin two-tab change/delete/clear/no-echo browser evidence at the head SHA | W1 behavior retained (no native adapter added) |
| Bilingual foundation browser capture | captured: real-browser story screenshots for saved-en-in-Chinese-env, saved `zh-CN` (CJK font glyphs) and preview-unset English | W2a adds real-app shell/dialog captures across en/zh, 1440x900/390x844 and light/dark (exact count bound to the pushed `receipt.json`) |
| Production startup first-paint | captured: real `main.tsx`/`createBrowserRouter` startup with synthetic API stub; first COMMITTED bilingual consumer text and `<html lang>` asserted together | W2a reads the ACTUAL first committed Sidebar/AppBar DOM (frozen on first connection, never overwritten by a later correction) with `<html lang>` and the real navigator read-back; a causal `I18N_W2A_EVIDENCE=negative` control proves the same predicate rejects an initially-wrong shell |
| Mounted-shell switching state | N/A (foundation) | captured: help non-default tab (S16), AddOrg typed slug + mapped error (S12 wide-light; S17 zh-narrow-light/en-narrow-dark/zh-wide-dark) and palette query + non-default selected row (S13 wide-light; S18 zh-narrow-light/en-narrow-dark/zh-wide-dark) preserved across storage-path locale switches with the actual `document.activeElement`, retained DOM node identity, open state and selection/value observed before AND after each direction; each help/AddOrg switch window asserts no `PUT /settings/org` and no `POST /api/v1/orgs`, and each palette switch window asserts zero `/api/` requests (cache-only), per direction |
| Native Mac persistence receipt | N/A — N0/N1 (Linux host; not claimed) | NOT RUN — N0/N1 still open |

## 10. Exclusions

No new dependency, daemon/API/schema/auth/permission/transport change, theme or
draft migration, native chrome, CLI/manual translation, route-family
translation campaign (W2b onboarding/W2c Settings/W3-W4 remain open), preview
enablement or public selector, deployment, or caller migration of display
formatters beyond the translated shell. Existing query/data/auth bootstrap
semantics are preserved; locale switching issues no `PUT /settings/org` and no
`POST /api/v1/orgs`, and the command palette's cache-only switch issues no
`/api/` request in the measured window.
