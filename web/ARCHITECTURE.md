# HappyRanch web — architecture notes

Localhost React SPA bundled into the FastAPI daemon. See
`docs/superpowers/specs/2026-05-14-web-ui-design.md` for the full web-UI
design and `web/DESIGN_SYSTEM.md` for the design-system migration plan.

## Layers (strict)

1. **`src/lib/api/<X>.ts`** — Daemon route mirror. One TS module per
   `runtime/daemon/routes/<X>.py`. Pure functions over a shared `request()` helper.
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
5. **`src/lib/`** — Feature-neutral helpers, including `utils.ts` (`cn`) and
   `modelClassification.ts` (the canonical token-rollup model labels shared by
   Usage and Dashboard).

## Boundary rule

> Every browser-callable daemon route maps 1:1 to one TS function in
> `src/lib/api/`. Features compose those functions through TanStack Query
> hooks. Features may not call `fetch` directly. Cross-feature imports are
> forbidden — share through `@/shared/`, `@/design-system/`, or `@/lib/`.
>
> `@/shared/` modules may import hooks, lib, and design-system but never
> `@/features/`. Primitives may not import patterns, layouts, hooks, or
> `@/lib/api`. Patterns may import primitives but not layouts.

The repository-local `feature-boundaries/no-cross-feature-imports` ESLint rule
mechanically checks every static import and export-from declaration below
`src/features/<domain>/`. It resolves both `@/features/<domain>/...` aliases
and relative paths, allows same-domain and neutral-layer edges, and remains
active when a nearby comment disables `no-restricted-imports`. Type-only
imports are static declarations and are covered. Runtime `import()` expressions
are intentionally excluded because they are non-static loading; ordinary lint,
typecheck, build, and tests still cover their syntax and resolution.

## Internationalization (i18n)

The AppBar language selector beside the theme toggle is a controlled consumer
of `useI18n` (THR-118 seq88). It uses the existing Select primitive and catalog
endonyms English/简体中文 with native `lang` attributes; owned accessible names
and tooltips translate. Settings ▸ Preferences uses the same locale/setter.
No new state, adapter, storage listener or request path is introduced. Header
interaction moves focus to the selector; external changes preserve editable
focus/selection. Existing browser precedence/persistence/tab synchronization
and deferred native restart acceptance remain. The focused ordinary-build
header evidence extends the existing W4a CDP fixture and reuses W5 startup cases.

Ongoing delivery requires every new or changed app-owned string to ship en and
zh-CN in the same PR, including headings, actions, dialogs, tooltips, accessible
labels, validation, loading/empty/error states and generated narratives. Use
typed catalogs, named parameters, explicit plurals and locale-aware helpers;
pure shared UI receives localized props. Authored content, machine identifiers
and raw diagnostics remain verbatim. Both locale switch directions preserve
mounted nodes, drafts, focus, selection and original actions with zero
locale-triggered requests, mutations or transport restarts/messages.

The Web guide and PR template require affected-state evidence in both locales
at 390×844 and 1440×900, actual clipping-ancestor/text/control bounds and readable,
reachable Chinese layout. Untranslated owned copy or broken Chinese layout
requires `REQUEST_CHANGES`. Structural automation cannot assess translation
meaning or usability. Historical W1–W5 receipts and the finite accepted coverage
inventory retain their original scope; inventory is not rendering proof.

`owned-copy/no-untranslated-copy` uses the locked TypeScript AST to check JSX
children/static branches and visible text attributes, and known direct shared
UI mounts must pass their localized presentation props. Exceptions identify an
exact source path, declaration, slot, literal and reviewed raw/default reason;
the authoritative real-config test disables inline configuration and refuses
stale or overbroad exceptions while ordinary lint preserves other rules.

`scripts/i18n-source-inventory.mjs` reads shipping HTML and static configuration
without executing Vite's daemon reader. It supports the current single main
entry and aligned @/relative imports, reexports, literal lazy imports and static
JSX/render-return mounts. Route identity follows named and namespace imports and
reexports from `react-router-dom`, including parent paths across aliases. Root
`createRoot(...).render(...)` calls accept direct JSX or statically bound JSX
constants (including aliases and bound root handles); other arguments refuse
with their source before release. Uncalled declarations remain unmounted.
Entry/root/alias changes, computed route/lazy inputs and promoted test/story/catalog/prototype owners require explicit resolution.
Imports alone never qualify a dialog. `coverage.test.ts` feeds these actual
source sites into the pure full-release guard: new owners need exact qualified
identities, including index/wildcard collisions; every english-only namespace
refuses full release. Settings' outer wildcard owns loading/error copy while
its nested wildcard remains a copy-free redirect. Historical 21 translated/3
not-applicable namespaces remain an accepted subset with dynamic totals. The
scanner stays outside the browser barrel and makes no rendering/dataflow,
general bundler completeness, or translation-quality claim.

Current W5b browser contract: English and Simplified Chinese are available. The
entry selects full mode synchronously before the first React text and hands its
one resolution through App/AppShell. AppShell also selects full mode for later
storage changes, deletion, clear and invalid values. Saved en wins Chinese;
saved zh-CN wins English; otherwise ordered supported browser languages apply
(Chinese variants map to zh-CN, with English fallback). There is no startup
preference write or locale-keyed remount. API preview defaults and native
authority/acknowledgement sequencing are unchanged. W1–W4 receipts below retain
their historical builds and preview identities. W5a finite coverage (21 translated/
3 not-applicable inventory) is accepted with PR993; inventory is not exhaustive
rendering proof. Native N0/N1 remain deferred, deployment/live verification separate.

Web copy uses the first-party typed contract in `src/lib/i18n/` (`locale`,
`catalog`, `format`, `coverage`) plus the `I18nProvider` in `src/hooks/i18n.tsx`
(`<html lang>`, `setLocale`, `t`/`render`). English (`en`) and Simplified
Chinese (`zh-CN`) catalogs are static and co-loaded; keys are typed, parameters
are named, and plurals declare explicit per-locale forms with a parity check.

**W5a copy repair:** the mounted Jobs Run/Reject, Settings Assistant/Capacity/Organization, Tasks Cancel/Revisit/Resolve, Threads Archive/Invite/RemoveParticipant and shared NewThread dialogs pass the existing `common.close` label to their built-in close control. Their actions and focus-return behavior are unchanged. `Markdown` accepts optional `mermaidLoadingLabel`, and `MessageBubble.labels.mermaidLoading` forwards it. KB, Task detail/recall, Threads and both Assistant turn variants supply `common.mermaidLoading`; omitted props retain “Rendering diagram…”. A private Markdown-local context carries only that string to the stable code renderer. Suspense remains per Mermaid block, and changing locale preserves loaded diagrams without another render. Authored Markdown/code and raw Mermaid failure source stay verbatim, including text equal to either loading label. Coverage markers remain inventory; W5a rendering acceptance requires the finite mounted-state/browser evidence. W5a finite mounted coverage is accepted (PR993); W5b enables full browser resolution and bilingual availability copy. Desktop N0/N1 remain deferred. Pending-import assertions live in the isolated Markdown.loading.test.tsx file; Markdown.test.tsx SVG/error cases use an independent lazy-module instance so filters and file order do not depend on releasing another test’s import.
**W5a Work Hours reachability:** the agent-detail reconciliation heading and local edit controls wrap within the page, including long raw agent/team names. The reconciliation and overview roster tables keep every column, value and provenance cell in a localized, named, keyboard-focusable horizontal scroll region; focus the region and use the arrow keys to reach the rightmost columns. Shared Button/AppShell and editor/action semantics are unchanged. The earlier 390px document-width checks did not establish child/control or column reachability: historical clipping captures remain failed evidence. The affected ordinary-build browser case is `web/scripts/w4a-browser-evidence.mjs --slice work-hours-reachability`, checking actual control bounds, readable headings and keyboard access to the final column in en/zh-CN at 390/1440, with mounted editor/draft/focus/navigation preservation. W5a finite coverage was independently reviewed, QA-verified and manager-accepted with PR993; W5b enables full browser locale resolution. Historical evidence keeps its original source/build identity. The tier editor now stacks field rows below the small-screen breakpoint, bounds grid children and field/control groups, wraps resets/day controls and full raw title/provenance/selected-timezone text, and allows the existing dialog to scroll vertically. Desktop retains the row hierarchy. Editor child and text-range bounds must be measured against actual dialog content and viewport after scrolling, with pointer hit testing and a real Tab cycle; page/table bounds alone do not establish editor readability. The affected browser cases include all three tiers, windowed/continuous and impact stages, long raw values, both locales at390/1440, and both-direction node/focus/selection/draft preservation with zero locale HTTP. Actual Save/PUT, reset-null and verbatim422 contracts remain owned by TierEditorDialog.test.tsx and work-hours.i18n.test.tsx; screenshots alone do not prove writes.


The initial locale is resolved synchronously before the first React text
(`bootstrapDocumentLocale` in `src/main.tsx`) from the saved
`happyranch.ui.locale` value; production explicitly uses full mode, so absent/invalid saved choices follow
the ordered supported browser languages, then English. That one resolution
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
stayed English until W4 and is translated by W4b) and built the client-only Settings ▸ Preferences
language selector (`sections/PreferencesSection.tsx`). `SettingsPage` mounts the
`preferences` route OUTSIDE the `useSettings` loading/error/data gate, so it
works while the settings API is loading, failing or empty; the other panels
keep that gate. Until W3b-2 a `VITE_ENABLE_I18N_PREFERENCES` build gate
(`languagePreferenceGate.ts`, now removed) kept the selector out of ordinary
production builds.
**W3a** translated the mounted Dashboard and Threads route families (`features/dashboard/**`, `features/threads/**` list/detail/composer/strips/dialogs and the shared `shared/threads/NewThreadDialog.tsx` it mounts); pure design-system patterns (Composer, ThreadHeader, InboxRow, StatValue, CrescentMoonBadge, RecipientsInput, MentionTextarea, …) take optional localized label props with English defaults, so their other callers are unchanged. Thread errors are held as locale-neutral `ThreadErrorView` descriptors (`lib/threadErrors.ts`: mapped catalog key/params, or `raw` text rendered byte-for-byte even when empty or equal to a catalog string) and rendered at render time. Authored thread titles, message Markdown, names, IDs, filenames/hrefs, raw delivery payloads and machine values stay verbatim; a locale switch keeps drafts, attachments, selection, open dialogs and focus and issues no request. W3a browser evidence runs `scripts/w3a-core-browser-evidence.mjs` against the ORDINARY dist only (storage-event switching, no in-app instrumentation); since W3b-2 its gate case G asserts the ordinary bundle contains the Preferences selector, `/settings/preferences` renders it, and historically an unset Chinese navigator stayed English. W5b ordinary activation cases establish Chinese fallback; the former preview-dist positive control is gone with the flag.
**W3b-1** translated the mounted Tasks route family (`features/tasks/**`: list/detail panes, filters, status/rollup/fan-out presentation, states and the owned Cancel/Revisit/ResolveEscalation dialogs). `StatusBadge` takes an optional localized `waitingLabels` prop with an English default (Jobs/TaskCard callers unchanged). Dialog errors are `TaskErrorView` descriptors (`features/tasks/strings.ts`: mapped key; an unmapped daemon code or, with no code, a non-empty string diagnostic rendered verbatim; otherwise the localized fallback) rendered at render time; briefs, notes, names, IDs, machine values, unknown flavors/work-status states and raw event actions/payloads stay verbatim, and group/lineage React keys are locale-neutral so a switch keeps rows, dialogs, drafts and focus with no request. Browser evidence: `scripts/w3b-tasks-browser-evidence.mjs` against the ORDINARY dist.
**W3b-2** translated the mounted Jobs route family (`features/jobs/**`: list chrome/status groups/callout/columns/relative age, detail states/actions/command card/cascade/gated notice/rail/output, and the owned Run/Reject dialogs). Daemon values (IDs, titles, script text, rationale, agent names, status tokens, `exit <code>`, stdout/stderr, reasons) stay verbatim. Run/Reject/Stop errors are `JobErrorView` descriptors (`features/jobs/strings.ts` `classifyJobError`, the same boundary as `classifyTaskError`). W3b-2 also enabled the opt-in language preview: Settings ▸ Preferences ▸ Language is mounted in ordinary builds, at that historical stage an unset preference stayed English and the selector disclosed untranslated secondary pages. W5b supersedes both with full browser resolution and bilingual availability copy. Browser evidence: `scripts/w3b-jobs-browser-evidence.mjs` against the ORDINARY dist.

**W4a-1** translated the mounted Runtime Health and Dreams routes (`features/health/HealthPage.tsx`: header, history-window toggle, stat cards, uptime/relative-age units, loop and HTTP-latency tables, trends; `features/dreams/**`: header eyebrow plural, feed, status pills, counts, quiet state, overview rail and the dream detail drawer with its candidate review gate). Daemon values (loop names, route templates, dream IDs, agent names, local dates, summaries, transcripts, error text, candidate title/slug/topic/rationale/body, KB slugs, unknown status tokens) stay verbatim. Accept/Dismiss errors are `DreamErrorView` descriptors (`features/dreams/strings.ts` `classifyDreamError`, the same F1 boundary as `classifyJobError`); `DREAM_STRINGS` is replaced by `dreams.*` catalog keys. Browser evidence: `scripts/w4a-browser-evidence.mjs` against the ORDINARY dist; its `API_ROUTES`/`VIEW_ROUTES`/`SWITCH_ROUTES` tables are extended by later W4 slices (W4c Agents/Skills and W4d-1 KB/Artifacts shipped; Usage presentation W4d-2).

**W4b** translated the mounted Todos (`features/todos/**`: list, detail, status pills, rows, recurrence/timezone presentation, Confirm/Edit dialogs), Work Hours (`features/work-hours-config/**`: overview, wakes, agent detail, TierEditorDialog; plus the Work Hours-owned `shared/work-hours/EligibilityEditorDialog.tsx` that Settings ▸ Organization mounts and its `ErrorPanel`) and Audit (`features/audit/**`: page, timeline, filters and the narrative, whose sentences are catalog templates with interpolation) route families. Agent names, task/schedule IDs, actions, timezones, cron/recurrence values and raw payload/error values stay verbatim; every visible date/time goes through `lib/i18n/format.ts` (`formatDateShapeFor` named shapes; feature-local `Intl.DateTimeFormat('en-US' | 'en-CA')` remains only for timezone-conversion parsing and `<input>` values, enforced by a `format.test.ts` source scan). Count-bearing Todos/Audit copy uses per-locale plural objects selected by a numeric `count`, and the two Work Hours dialogs pass `closeLabel={t('common.close')}`. Error sites use the same F1 boundary through feature-local `strings.ts` classifiers. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

**W4c** translated the mounted Agents (`features/agents/**`: roster list, agent detail pane/drawer, pending enrollments, AddAgentDialog, TeamEscalationPolicyPage/Card) and Skills (`features/skills/**`: catalog, validation, skill detail + assignment panel, custom-skill list/create/detail) route families. User/daemon values (agent names, roles and team identifiers without translation or title-casing, team-policy bodies, contract ids, digests, skill names/slugs/descriptions/SKILL.md bodies, versions, provenance values) stay verbatim; visible dates/times, including policy release/activation history and custom-skill purge completion, use named shapes in `lib/i18n/format.ts` (`dateTime` for those timestamps; en/zh-CN rendered-state regressions complement the source scan, which rejects direct locale-formatting calls), count-bearing copy uses plural objects, in-scope dialogs pass `closeLabel={t('common.close')}`, and error sites use the F1 boundary through `classifyAgentError` / `classifySkillError`. Below `md` the Agents roster stacks above the detail pane (height-capped, internally scrolling) instead of a fixed 244px rail, so the detail is not squeezed at 390px. The detail main pane uses `max-md:min-h-0` only below `md`; at `md` and up its base computed min-height remains `auto`. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

The Agents pane is mounted on desktop and mobile; the retained drawer component shares `SystemPromptEditor` without a routing change. Prompt drafts freeze their base revision and captured org/target; the providers expose prompt-only PUT and explicit uncached roster readback for that identity. Saved requires matching receipt body and exact revision, never query invalidation alone. Validated uncached prompt observations reconcile only the captured org/target prompt and revision in the roster cache, so leaving and returning preserves that observed base. Unrelated fields and agents stay unchanged; a newer prompt observed during readback wins and requires another explicit inspection instead of Saved. Ordinary reads already in flight are cancelled without reverting cached observations before reconciliation. PUT plus readback disables draft controls. Errors retain the authored draft until local discard or explicit fresh inspection/reapply, and late results are isolated by identity. Prompt errors are locale-neutral descriptors and raw diagnostics remain verbatim; locale changes preserve the same node/body/focus/selection.

The assistant dock body and conversation controls are translated in en/zh-CN
with complete mounted-copy evidence; W5a finite audit is accepted and W5b enables full browser resolution. The mount-time coverage
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
event because there was no public selector at that time. W2c Preferences evidence runs
`scripts/w2c-preferences-browser-evidence.mjs` against two builds of the head
(`--preview-dist` for cases B–I and `--dist`; since W3b-2 removed the
`VITE_ENABLE_I18N_PREFERENCES` flag both are ordinary builds). Its case A
asserts the W3b-2 contract on the ordinary dist — the bundle contains the
Preferences markers, `/settings/preferences` renders the panel, both radios and
the sub-nav link, and historically an unset preference stayed English (before
W3b-2 it asserted the redirect and omission; W5b full-mode cases supersede that default); it drives the real radios with CDP input and records focus, node
identity, `<html lang>`, persistence status and a zero-request switch window,
plus causal negatives for a wrong locale, a remount and an API write. It also
drives Settings ▸ Organization and ▸ Executors: an already-visible Work Hours
success banner must re-translate on a second-tab `storage` switch in both
directions (same banner/switch/dirty-input nodes, value, focus, one PUT, zero
switch-window requests), and raw executor diagnostics byte-equal to the English
fallback must stay verbatim. With `--defect-dist` (a preview build of a head
that stored the pre-translated banner) the same banner predicate must fail;
an injected translated diagnostic must fail the raw predicate. Every page installs and asserts the real
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


**W4d-1** translates KB and Artifacts chrome, including list/detail/candidates,
search/filter/breadcrumb/empty/loading/error/Retry, gated KB Compose labels and
its supported DialogContent close control, and artifact upload/download/delete
confirmation. `VITE_ENABLE_KB_COMPOSE` remains unchanged. Candidate/Compose and
artifact action errors are pure F1 category/raw descriptors rendered through the
current translator, so generated messages retranslate without repeating a
mutation; unknown codes and nonblank raw diagnostics remain verbatim, while
absent/blank diagnostics use the localized fallback. KB authored content,
type/topic/tags/slug/names/IDs and artifact canonical names, path segments,
file-derived titles/provenance identifiers remain byte-verbatim. Only artifact
client-derived type LABELS translate; classification/parsing and stored data
remain unchanged. Counts use explicit-locale display plus plural objects.
KB card/detail share relative-age rounding and catalog units, with explicit
unavailable display for invalid dates. Artifact filename calendar dates use the
existing `monthDayYear` shape in UTC without shifting the day; invalid calendar
dates render an unavailable label. Modified times use viewer-local `dateTime`
with explicit locale. The English modified-time shape now zero-pads the hour
(e.g. `02:05 PM`), and invalid KB age displays `Age unavailable` instead of
`NaNd`; F1 replaces synthetic HTTP action failures with raw diagnostics or
localized fallbacks. Artifact bytes use `formatAttachmentSizeFor` with the same
binary thresholds/rounding; other attachment consumers retain their legacy
optional-locale behavior. DrawerContent has no closeLabel/control; Compose's
DialogContent does, and is localized. The KB drawer fits below 640px without
changing desktop width. At that narrow breakpoint the scrollable filter rail
stacks above the feed, and the header actions stack below the title so list
copy stays readable; desktop retains the side rail and header layout. Raw KB
type badges retain their stored case (for example, `sop` rather than `SOP`)
in both locales instead of applying a CSS uppercase transform. `kb` and
`artifacts` coverage is translated; `usage` is translated by W4d-2; `system-assistant` is translated by the mounted dock/conversation slice. W5b supersedes the historical preview default with full browser resolution
and bilingual availability disclosure; native N0/N1 remain deferred.
Browser evidence adds representative KB list/detail/candidates and artifact
list/folder/upload rows to `web/scripts/w4a-browser-evidence.mjs`, plus one
upload filename/selected File/focus/control-preservation switch in both directions
with zero requests. Final-head evidence is recorded in the task handoff.


### Usage presentation locales (THR-118 W4d-2)

The Usage v1 page translates app-owned Workload/Efficiency labels, columns,
run types, statuses, comparison/coverage explanations, help, retry/stale/empty
copy, ARIA, counts and units through `usage.*`. Raw agents, CLIs, model names,
authored values and timezone identifiers remain verbatim. Window `*_local`
parts already describe org-local wall-clock time and never shift with viewer
TZ; UTC instants use the response timezone and explicit locale. Invalid-format
fallbacks keep their original behavior. Compare/cohort/default/manual selection
and all query/refetch/server metric semantics are unchanged; locale changes
preserve nodes, focus and selection without new API calls. Usage is translated; the mounted Assistant dock and conversation controls are
also translated. W5b enables full browser-language defaults; native N0/N1 remain deferred. Browser evidence:
`web/scripts/w4a-browser-evidence.mjs --slice usage` (ordinary bundle).

Settings uses the existing five sub-nav links above the panel below640px, wrapping
without changing routes, labels, icons or active state. At640px and wider it
retains the rail/panel layout. Preferences uses local minimum sizing, wrapping
and narrow-screen padding; all copy remains visible and the API-independent
route/state/persistence contracts are unchanged.

## What is intentionally not in here

- Agent-callback endpoints (`/report-completion`, `/manage-agent`,
  `/manage-repo`, `/dispatch`, `/learning add|update|promote`, thread
  `/reply`, `/decline`, `/dispatch`, `/close-out`). Those are agent-subprocess
  only and would be a privilege-escalation if exposed in the browser.
- `--as-founder` impersonation surface for KB deletes. Stays TTY-gated in CLI.
- Multi-user concerns: login screens, account model, RBAC. Localhost only.

### System Assistant mounted copy (THR-118)

The global `AssistantDockHost` and its mounted `ConversationSwitcher` bind en/zh-CN app-owned visible/accessibility copy, including composer/state/key hints, typing/tool activity and conversation actions/rename/delete confirmation. Shared MessageBubble/TypingBubble copy overrides are reused. Errors retain capture-time provenance: app fallback keys resolve at render time; raw daemon detail/message and caught diagnostic values (including empty or catalog-equal values) stay exact. Executor/tool names, titles, authored content and IDs remain data. Viewer-local timestamp and elapsed semantics remain unchanged.

Locale switches preserve mounted nodes, active conversation, transcript/inflight state, drafts, focus and selection without entering connection-effect dependencies or issuing requests/mutations/reconnects. The detailed boundary is [Assistant Web UI §6.12](../docs/superpowers/specs/2026-06-12-system-assistant-web-ui-design.md#612-mounted-dock-locale-presentation-thr-118). Ordinary-build evidence is `scripts/w4a-browser-evidence.mjs --slice assistant`: real HTTP/WS seams and request/socket ledger plus 390/1440 screenshots. Coverage inventory includes both consumers and marks only `system-assistant` translated in this slice, supported by component regressions and ordinary-build mounted-copy evidence. W5b enables full browser-language defaults; native restart acceptance remains deferred.
