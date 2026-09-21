# Web i18n — W1 foundation contract

> Status: current (W1 implementation)
> Current Source: `web/src/lib/i18n/`, `web/src/hooks/i18n.tsx`, this spec.
> Supersedes: the `Internationalization layer` non-goal in
> `2026-05-14-web-ui-design.md` (historical text preserved with a supersession
> annotation).
> Notes: W1 ships the foundation only. Route/page translation is W2-W4; native
> preference persistence is N1. This spec describes the W1 contract and is
> updated per phase.

## 1. Scope

THR-118 implements a first-party, typed English (`en`) / Simplified Chinese
(`zh-CN`) contract for the web console. This document covers **W1 — foundation,
contract and test harness**. It intentionally does **not** translate any route
or page, ship a public language selector, enable the preview selector, or touch
the native app.

Delivery status at W1 (keep separate from later phases):

| Area | W1 status |
| --- | --- |
| Typed `en`/`zh-CN` catalog, named params, explicit plurals, parity check | shipped |
| Synchronous initial resolution + `<html lang>` | shipped |
| Browser preference (`happyranch.ui.locale`) with resilient storage | shipped |
| Injectable locale preference adapter (native seam) | shipped (browser + test doubles only) |
| Mounted-route/namespace coverage manifest | shipped |
| Foundation browser/Storybook evidence (isolated, non-product) | shipped — `web/scripts/i18n-browser-evidence.mjs` + `I18nFoundation.stories.tsx` |
| Explicit-locale display formatters | shipped (interfaces only; no caller migration) |
| Route/page translation | **W2-W4** — not shipped |
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
(`RootRedirect` "Loading…") and the app catch-all `*` (`NotFound`) are
`english-only`, never `not-applicable`; the copy-free `NavigateToHome`/
`SpendRedirect`/`ScheduleRedirect` and the settings-internal redirects are
`not-applicable`. Tokens that collide across modules are disambiguated with
`<scope>:<token>` qualified identities (`routes.tsx:*` vs `SettingsPage.tsx:*`,
`SettingsPage.tsx:index`, `SettingsPage.tsx:agents`).

Dialog/overlay surfaces are the ACTUAL mounted component names, and the test
anchors them to the real consumer sources (e.g. `ThreadsPage.tsx` mounts
`NewThreadDialog`/`InviteDialog`/`ArchiveDialog`/`RemoveParticipantDialog`;
`TaskDetailPage.tsx` mounts `CancelTaskDialog`/`RevisitTaskDialog`/
`ResolveEscalationDialog`; `JobDetailPage.tsx` mounts
`RunJobDialog`/`RejectJobDialog`), so an invented or unreachable name fails the
test. In W1 no product surface is `translated`: every namespace is visibly
marked `english-only`, so English fallback is never mistaken for coverage.

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
bundle (`web/dist`) with a synthetic `/api/v1` stub. It asserts saved explicit
English under a Chinese environment, saved `zh-CN`, unset preview English,
first React text agreeing with `<html lang>` for the real
`main.tsx → App → createBrowserRouter` startup, state preservation across a
locale switch, fail-safe storage read/write failure, real same-origin tab
change/delete/clear with no echo write, and that Chinese copy is rendered by a
CJK platform font (captured PNGs + `receipt.json` bound to the head SHA under
the task evidence directory). This is foundation evidence only: it does not
claim translated product routes (none exist in W1).

Frontend readiness map for foundation-only scope (actual evidence):

| Readiness item | W1 |
| --- | --- |
| Route-wide Chinese rendering | N/A — no route translated in W1 (manifest marks every namespace `english-only`) |
| Public language selector | N/A — W3 |
| Browser two-tab live evidence | covered by unit/integration storage-event tests AND captured same-origin two-tab change/delete/clear/no-echo browser evidence at the head SHA |
| Bilingual foundation browser capture | captured: real-browser story screenshots for saved-en-in-Chinese-env, saved `zh-CN` (CJK font glyphs) and preview-unset English |
| Production startup first-paint | captured: real `main.tsx`/`createBrowserRouter` startup with synthetic API stub; first React text and `<html lang>` agree |
| Native Mac persistence receipt | N/A — N0/N1 (Linux host; not claimed) |

## 10. Exclusions

No new dependency, daemon/API/schema/auth/permission/transport change, theme or
draft migration, native chrome, CLI/manual translation, route translation
campaign, preview enablement, deployment, or caller migration of display
formatters. Existing query/data/auth bootstrap semantics are preserved.
