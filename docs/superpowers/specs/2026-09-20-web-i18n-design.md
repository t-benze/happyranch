# Web i18n — foundation + W2a shell + W2b onboarding + W2c Settings + W3a Dashboard/Threads + W3b-1 Tasks + W3b-2 Jobs/preview + W4a-1 Health/Dreams + W4b Todos/Work Hours/Audit + W4c Agents/Skills + W4d-1 KB/Artifacts + W4d-2 Usage + W5b full browser activation contract

> Status: current (W1 foundation + W2a mounted-shell + W2b onboarding + W2c Settings/Preferences + W3a Dashboard/Threads + W3b-1 Tasks + W3b-2 Jobs migration and opt-in preview + W4a-1 Health/Dreams + W4b Todos/Work Hours/Audit + W4c Agents/Skills + W4d-1 KB/Artifacts + W4d-2 Usage)
> Current Source: `web/src/lib/i18n/`, `web/src/hooks/i18n.tsx`, the mounted
> shell/onboarding consumers, this spec.
> Supersedes: the `Internationalization layer` non-goal in
> `2026-05-14-web-ui-design.md` (historical text preserved with a supersession
> annotation).
> Notes: W1 shipped the foundation; **W2a** translated the mounted shell
> (AppBar/Sidebar/root/not-found/ErrorBoundary/AddOrgDialog/help/palette);
> **W2b** translated the onboarding route (`/onboarding` plus the shared
> ConnectFlow it mounts); **W2c** translated Settings and built the
> Preferences language selector (production-gated until W3b-2);
> **W3a** translated the mounted Dashboard and Threads route families;
> **W3b-1** translated the mounted Tasks route family;
> **W3b-2** translated the mounted Jobs route family and enabled the opt-in
> preview (Preferences ▸ Language in production, English when unset, with a
> secondary-pages coverage disclosure; historical default superseded by W5b).
> **W4a-1** translated Runtime Health and Dreams; **W4b** translated the
> mounted Todos, Work Hours (incl. the shared `EligibilityEditorDialog`) and
> Audit route families. **W4c** translated the mounted Agents
> (`agents`, `agents/:agent_name`, `agents/:agent_name/team-escalation-policy`)
> and Skills (every `skills*` token) route families; KB/Artifacts are translated by **W4d-1**; Usage presentation is translated by **W4d-2**.
> The mounted Assistant dock/conversation controls are translated. W5a finite
> mounted coverage is accepted (PR993); W5b enables full browser resolution at
> the entry and AppShell and replaces the obsolete secondary-page disclosure.
> Native preference persistence (N0/N1) remains deferred; deployment/live
> verification is separate. The W1 sections below are retained as the historical W1 contract
> and updated per phase.

The THR280 prompt editor reuses the mounted Agents pane at desktop/mobile and the retained drawer component without mounting changes. Prompt-local en/zh-CN actions, initial states, pending/readback/conflict/failure guidance use catalog keys; authored multiline bodies and raw daemon diagnostics stay verbatim. Locale switching must preserve the same draft node, body, focus and selection without a request. Draft bases are frozen, controls remain disabled through PUT plus owned fresh readback, and Saved requires receipt body/revision agreement. Recoverable drafts and local navigation/discard semantics remain; no automatic retry or locale-triggered mutation. Validated uncached prompt observations reconcile only the captured org/target prompt and revision in the roster cache, so leaving and returning preserves that observed base. Unrelated fields and agents stay unchanged; a newer prompt observed during readback wins and requires another explicit inspection instead of Saved. Ordinary reads already in flight are cancelled without reverting cached observations before reconciliation.

## 1. Scope

### Header language selector (THR-118 seq88)

The shipping AppBar places a compact controlled Select immediately before its
theme toggle, in both default and tasks presentations. English and 简体中文
remain native endonyms in both catalogs and carry their own `lang`; the active
endonym stays visible and the open options indicate the current choice.
The accessible name/tooltip translate. Keyboard selection, Escape dismissal
and focus return come from the existing Select primitive. Selection applies
immediately through the shared locale setter; Settings ▸ Preferences remains
available and synchronized in both directions, even when its API has no data,
is pending or has failed. There is no additional locale state, persistence or
storage listener, route remount, request or transport behavior. Header use
deliberately moves focus; other-tab language changes preserve focused editors.
Browser reload, explicit saved-choice precedence and full-mode missing/invalid
Chinese browser defaults retain their existing contracts. Native restart
persistence remains deferred under seq39; deployment is separate.

Focused acceptance uses the existing Settings component tests and the W4a
ordinary-build `header-language` selection (default/tasks header geometry,
pointer/keyboard/theme/agreement/reload/tabs, retained multiline Agent editor
and original Cancel/navigation), plus existing W5 startup and provider/locale
keepers. Both languages, 390×844/1440×900 and comfortable/compact density require
real control/text/clipping bounds and individually inspected screenshots.

### Ongoing bilingual development safeguards (THR-118 seq81/82)

Every new or changed app-owned heading, action, dialog, tooltip, accessible
label, validation, loading/empty/error state and generated narrative ships
English and Simplified Chinese together in the same PR. Typed catalogs, named
parameters, explicit plurals and locale-aware helpers own presentation. Pure
shared UI receives localized props; authored content, machine identifiers and
raw diagnostics remain verbatim.

The maintained Web guide and PR template require both locales for applicable
affected populated/loading/empty/error states at 390×844 and 1440×900. Acceptance
includes actual text/control bounds against clipping ancestors, whole Chinese
readability after permitted scrolling, pointer/keyboard reachability and original
navigation/actions. Both locale switch directions preserve mounted nodes, drafts,
focus and selection with zero switch-window HTTP of any method, mutations and
transport restarts/messages. Retained DOM references are observed before fresh
lookup or refocus. Untranslated owned copy or broken Chinese layout requires
`REQUEST_CHANGES`; automation checks supported structure/completeness while human
review owns meaning and usability. Historical phase receipts retain their scope,
and finite namespace inventory never claims exhaustive rendering or dataflow
proof. Native persistence and live deployment remain separate.

The bounded TaskCard repair supplies existing age/lineage/waiting catalog values
from the mounted Agents pane; standalone pattern defaults and chained rounding
remain unchanged. The Jobs cascade supplies existing waiting labels and retains
its blocked-on-job filter, raw statuses and task destinations. Focused ordinary
browser selections are `w4a-browser-evidence.mjs --slice agents-safeguard` and
`w3b-jobs-browser-evidence.mjs --slice cascade`. They cover applicable task states
and en/zh-CN at 390/1440 without the historical whole-console run. The named
`C9 Agents SystemPromptEditor locale preservation` case uses a valid revision,
authored multiline draft and selection 3:8; observes retained-DOM connectedness
before lookup/refocus/reopen; checks both directions and HTTP/WS/SSE silence;
and retains original Cancel/navigation. Save receipt/readback contracts remain
owned by the unchanged SystemPromptEditor tests. A temporary shipping Pane
locale-key remount is the causal RED control; every mutated production byte is
restored and the ordinary build rebuilt before GREEN. Receipts determine actual
execution status; source bindings and screenshots alone are not acceptance.

The locked ESLint/TypeScript source guard covers literal JSX/static branches,
visible text attributes and known direct default-bearing shared UI mounts.
Raw/default exceptions require exact path, declaration, slot, literal and reason;
stale/overbroad exceptions fail the authoritative real-config test with inline
configuration disabled. The Node-only inventory reads shipping HTML and static
Vite/TS aliases, resolves supported relative/reexport/literal-lazy owners and
finds JSX/render-return route/dialog sites rather than counting imports. Router
named/namespace imports and reexports retain route identity and parent paths.
Additional createRoot renders in reachable static runtime imports/reexports
(including side-effect imports and transitive/cyclic module graphs) discover
direct JSX or statically bound JSX constants (including aliases and bound root
handles), or refuse unhandled arguments with their source. Type-only edges are
excluded; evaluating an imported module does not mount unused JSX declarations
or enter uncalled functions/classes. Changes
outside those supported static shapes and fixture/prototype promotion refuse.
New source owners require qualified classification; the outer Settings wildcard
is translated loading/error chrome and its nested wildcard is the existing
copy-free redirect. Full release refuses english-only namespaces while preserving
the historical 21 translated/3 not-applicable subset and truthful bilingual
coverage markers. These are finite source completeness gates, not rendering,
dataflow, bundler completeness or translation-quality proof.

THR-118 implements a first-party, typed English (`en`) / Simplified Chinese
(`zh-CN`) contract for the web console. **W1** covered the foundation,
contract and test harness; it intentionally did not translate any route or
page, ship a public language selector, enable the preview selector, or touch
the native app.

**W2a** translated the already-mounted shell presentation into
both locales while retaining the existing layout and interactions: the AppBar
page titles and controls, Sidebar navigation/aria/org-switcher/account copy,
the root loading and not-found fallback, the ErrorBoundary fallback copy, the
AddOrgDialog, and the shared help/palette presentation strings. It added
CJK-capable SYSTEM font fallbacks to the design tokens (no webfont download or
dependency) and marked the migrated namespaces `translated` in the coverage
manifest.

**W2b** (this phase) translates the onboarding route presentation into both
locales: `OnboardingPage` (first-run/returning welcome, create → creating →
success, the read-only broken-org list, the executor-prereq readiness panel),
the `ConnectRuntimeStep` wrapper chrome, and the shared
`shared/connect/ConnectFlow.tsx` bodies (built-in/custom mode selection,
form/waiting/committing/connected/retryable/terminal-failure/cleared states,
copy feedback, status text and aria labels). It extracts the single
`web/src/lib/addOrgError.ts` classifier/renderer shared by AddOrgDialog and
the onboarding create step. Because ConnectFlow is shared with Settings ▸
Executors, translating it localizes that connect body too; at W2b (historical
state, superseded by W2c below) `settings` remained `english-only` (the
surrounding Settings chrome/sections were not yet translated) and
Settings/Preferences was not claimed as covered. User-entered
slugs, registered tool names/paths, the slug regex, broken-org raw errors and
the generated copy-paste CLI prompt (raw step ids, tokens, route targets) stay
byte-for-byte verbatim; mapped categories re-translate on a switch with no
resubmission. Neither phase
translates the assistant dock body (W4) or any route family (W3/W4); neither
adds a public language selector; W3b-2 later opens the selector, and at that historical stage an unset
preference rendered English (superseded by W5b full mode).

Delivery status at W1 (keep separate from later phases):

| Area | W1 status |
| --- | --- |
| Typed `en`/`zh-CN` catalog, named params, explicit plurals, parity check | shipped |
| Synchronous initial resolution + `<html lang>` | shipped |
| Browser preference (`happyranch.ui.locale`) with resilient storage | shipped |
| Injectable locale preference adapter (native seam) | shipped (browser + test doubles only) |
| Mounted-route/namespace coverage manifest | shipped |
| Foundation browser/Storybook evidence (isolated, non-product) | shipped — `web/scripts/i18n-browser-evidence.mjs` + `I18nFoundation.stories.tsx` + an `I18N_BROWSER_EVIDENCE`-gated test-only first-commit consumer |
| Explicit-locale display formatters | shipped (W1 interfaces; display callers migrated in the translated shell/onboarding and, in W3a/W3b-1, Dashboard/Threads/Tasks) |
| Mounted shell translation (W2a: AppBar/Sidebar/root/not-found/ErrorBoundary/AddOrgDialog/help/palette) | **shipped — W2a** |
| CJK-capable system font fallbacks (no download) | **shipped — W2a** |
| Onboarding translation (W2b: OnboardingPage/ConnectRuntimeStep/shared ConnectFlow) | **shipped — W2b** |
| Settings translation (W2c: page chrome + Assistant/Organization/Executors/Capacity sections) | **shipped — W2c** (Work Hours-owned `EligibilityEditorDialog` stayed English until W4; translated by W4b) |
| Settings ▸ Preferences language selector (W2c) | **shipped — enabled in production by W3b-2** (the W2c `VITE_ENABLE_I18N_PREFERENCES` gate is removed) |
| Dashboard + Threads route families (W3a: DashboardPage, ThreadsPage list/detail/composer/strips, Archive/Invite/RemoveParticipant dialogs, shared NewThreadDialog) | **shipped — W3a** |
| Tasks route family (W3b-1: TasksPage/TaskListRow, TaskDetailPage incl. recall/events/fan-out/rail, Cancel/Revisit/ResolveEscalation dialogs) | **shipped — W3b-1** |
| Jobs route family (W3b-2: JobsPage, JobDetailPage incl. cascade/gated notice/rail/output, Run/Reject dialogs) | **shipped — W3b-2** |
| Runtime Health + Dreams (W4a-1: HealthPage; DreamsPage feed/rail + DreamDetailPane drawer) | **shipped — W4a-1** |
| Todos + Work Hours + Audit (W4b: TodosPage/TodoDetailPage/TodoRow/StatusPill + Confirm/Edit dialogs; Work Hours OverviewPage/WakesView/AgentDetailPage + TierEditorDialog + shared EligibilityEditorDialog; AuditPage/AuditTimeline/filters + catalog-templated narrative) | **shipped — W4b** |
| Agents + Skills (W4c: AgentsPage/AgentDetailPane/AgentDetailDrawer/PendingEnrollmentsTab + AddAgentDialog; TeamEscalationPolicyPage/Card; SkillsPage/SkillValidationPage/SkillDetailPage/SkillAssignmentPanel and the custom-skill list/create/detail pages) | **shipped — W4c** |
| KB + Artifacts chrome (W4d-1) | **shipped — W4d-1** |
| Usage chrome (W4d-2) | **shipped — W4d-2** |
| Mounted Assistant dock + conversation controls | en/zh-CN app-owned visible and accessible copy; component and ordinary-build browser evidence in the task handoff |
| Final mounted-console audit | **W5a accepted — PR993**; W5b enables full-mode browser-language defaults |
| Public language selector / opt-in preview | **W3b-2 historical preview; superseded by W5b** (bilingual availability and browser fallback) |
| Full-mode automatic environment detection | **enabled — W5b** at the production entry and AppShell; API preview defaults remain |
| Native preference persistence (Swift/message handler) | **N0/N1** — not shipped |

**W3a** translates the mounted Dashboard and Threads route
families — Dashboard cards/narratives/actions and loading/first-run/error/Retry
copy; Threads list/detail panes, filters, pin sections, status presentation,
reply-delivery/responder strips, the reply Composer, rename/pin/archive/resume/
invite/remove flows and the shared `NewThreadDialog`, including accessibility
copy — through the typed catalog with named params and explicit plurals. Pure
design-system patterns receive localized labels as optional props (English
defaults keep other callers unchanged); no hook enters a primitive/pattern.
Thread errors are held as `ThreadErrorView` descriptors from
`lib/threadErrors.ts` (`classifyThreadError` → mapped catalog key/params or
`raw` text; `renderThreadError(view, t)` at render time), so a mapped
validation/error message re-translates in place without resubmission while a
raw daemon diagnostic (`HTTP <status>` for unmapped codes, a non-ApiError
string, an empty string or one byte-equal to catalog copy) is shown verbatim.
Authored thread titles, message Markdown, speaker/participant/agent names,
thread/task/job/artifact IDs, filenames/paths/hrefs, raw reply-delivery/audit
payloads, machine/status values and keyboard shortcut sequences stay
byte-for-byte; display-only dates/counts use the explicit-locale formatters.
A locale switch never keys/remounts the page, never loses selection, drafts,
attachments, open dialogs or focus, and issues no request. The CLAUDE.md
thread rename/pin invariants are untouched. At W3a the Preferences gate stayed
closed (opened by W3b-2) and unset stayed English; W5b supersedes that default.

**W3b-1** (this phase) translates the mounted Tasks route family (`tasks`,
`tasks/:task_id`): the list heading/eyebrow, group-by and filter form, status
group labels, the Waiting-on-you attention group and its count, column header,
relative age, waiting/worst-child rollup context and lineage links; the detail
header, actions and note labels, chain timeline, revisit/dependency lineage,
brief, execution subtasks, recall tree, activity log states and the two
prettified fan-out event labels, the fan-out band and progress line, the jobs
section, the property rail and the execution-status card; loading/empty/error/
end-of-list states; the owned Cancel/Revisit/ResolveEscalation dialogs; and
their accessibility names. The shared `StatusBadge` takes an optional localized
`waitingLabels` prop whose English default keeps the Jobs and TaskCard callers
unchanged. Dialog errors are held as `TaskErrorView` descriptors
(`features/tasks/strings.ts`: `classifyTaskError` maps a recognized product
code to its catalog key; an unmapped daemon code, or — with no/empty code — a
non-empty string diagnostic (an `ApiError` string `detail`, a plain `Error`
message or a thrown string, never `ApiError`'s synthetic `API <status>` message)
renders verbatim, even when it equals catalog copy; only an absent or blank
diagnostic falls back to the localized per-dialog failure key) and the Revisit
session-timeout validation is a state-held key, so both re-translate in place
without resubmission. Briefs, summaries, failure/escalation notes, agent names,
task/thread/job IDs, job titles, status/block-kind/verdict/reason machine
values, an unknown escalation flavor, an unknown work-status state (the daemon
`label` is shown), raw event actions and payload JSON, hrefs and shortcut
sequences stay byte-for-byte. Group React keys stay the locale-neutral sentinels
and lineage keys use semantic role ids, so a locale switch re-labels the same
nodes, keeps selection, open dialogs, typed drafts and focus, and issues no
request. Display dates use the explicit display locale. At W3b-1 the
Preferences gate stayed closed; W3b-2 (below) owns Jobs plus the opt-in
preview.

**W3b-2** (this phase) translates the mounted Jobs route family (`jobs`,
`jobs/:job_id`): the list eyebrow/heading, the needs-you callout and its count,
the queue-clear line, the column header, status group labels (also the group
`aria-label`), the needs-review pill and the pending relative age; the detail
loading/error/not-found states, back link, header actions (Stop / Reject /
Approve & run / Run), command-card chrome and note, rejection/failure reason
labels, the If-approved cascade (loading/error/empty/count title), the gated
notice, the property rail labels and its yes/no/unbounded/seconds values, the
output panel chrome (title, waiting/loading, `(empty)`); and the owned
Run/Reject dialogs. Daemon values stay byte-for-byte: job/task IDs, job titles,
script text, rationale, agent names, interpreter and cwd values, job status
tokens (the `StatusBadge` and list outcome tokens `running`/`rejected`/`failed`,
`exit <code>`), exit codes, stdout/stderr and their stream names, the live
`[done] <status> exit=` log line, and rejection/failure reasons. Run/Reject and
Stop errors are `JobErrorView` descriptors (`features/jobs/strings.ts`:
`classifyJobError` applies the same F1 boundary as `classifyTaskError` — a
recognized code (`unknown_job`, `not_pending`, `not_running`,
`invalid_timeout`, `empty_reason`, `reason_too_long`) maps to its catalog key;
any other non-empty code renders raw; with no/empty code a non-empty string
diagnostic (`ApiError` string `detail`, `Error` message or thrown string, never
`ApiError`'s synthetic `API <status>` message) renders verbatim; otherwise the
localized per-action fallback). This replaces the former English-only
`Error <status>: API <status> (<code>)` rendering. The Reject validation
messages are state-held keys. A locale switch keeps the same cards, dialogs,
typed drafts and focus and issues no request; display dates use the explicit
display locale.

Historically, W3b-2 **enabled the opt-in language preview** in ordinary production
builds: `languagePreferenceGate.ts` and the `VITE_ENABLE_I18N_PREFERENCES`
flag are removed and Settings ▸ Preferences ▸ Language is always mounted. With
no saved choice the UI stays English — production resolves in `preview` mode,
so the browser/system language is never consulted (automatic detection remains
W5 `full` mode). The selector shows a localized coverage disclosure
(`settings.preferences.coverageDisclosure`: secondary, not-yet-translated pages
may still appear in English). W5b supersedes this historical default and disclosure
with full browser resolution and bilingual availability copy.

**W2c** translates the Settings surface and prepares the W3b-2
selector:

- `SettingsPage` header/meta, sub-nav heading and labels, API loading/error
  copy and panel headings/descriptions; the Assistant, Organization,
  Executors (registered list, custom profiles, binary paths) and Daemon /
  Capacity section bodies, including loading/empty/error, dialog, badge and
  accessibility copy. Raw daemon errors (`ApiError.message`, detail),
  identifiers, executor/agent/profile names, paths, commands, config keys,
  revisions and every capacity number stay byte-for-byte verbatim; messages held
  in component state are stored as keys/params (or locale functions) so an
  already-visible message re-translates on a switch without resubmission. The
  Organization Work Hours success banner is held as a boolean status; the
  Executors remove/validate/register failures are held as
  `{ raw: string } | { key: MessageKey }` (the Assistant `RegisterError` shape),
  so a product fallback re-translates while a raw diagnostic — even one whose
  bytes equal a catalog string, or an empty one — is shown verbatim and never
  replaced by fallback copy.
- `sections/PreferencesSection.tsx`: a fieldset of two native radios whose
  labels are the endonyms `English` / `简体中文` (each with its own `lang`, not
  translated), described by localized help, with a localized `role="status"`
  persistence line (pending / saved in this browser / failed — applies to this
  session only). Selecting calls the W1 `setLocale` only: immediate apply,
  `<html lang>` update, no remount, no org-settings write and no API request.
- Shell split: `SettingsPage` still calls `useSettings()` (unchanged fetch), but
  the `preferences` route is declared beside a `*` route that holds the
  existing loading/error/data gate and nested routes verbatim, so Preferences
  renders while the query is loading, errored or empty; every other panel keeps
  its prior gate, index/system/agents/unknown redirects and replace semantics.
- **Closed production gate (W2c; removed by W3b-2).** `languagePreferenceGate.ts`
  (`isLanguagePreferenceEnabled()`) reads the build-time
  `VITE_ENABLE_I18N_PREFERENCES === 'true'` flag (same pattern as
  `VITE_ENABLE_PROTOTYPES`/`VITE_ENABLE_KB_COMPOSE`). Without it the
  Preferences sub-nav entry and route are not mounted, a direct
  `/orgs/:slug/settings/preferences` URL is replace-redirected to Assistant by
  the existing catch-all, and the ordinary production bundle contains no
  Preferences component (only catalog strings). Vitest activated it with
  `vi.stubEnv` and the W2c harness built a separate preview dist (since W3b-2
  both are unnecessary: the harness case A asserts the mounted selector). W3b-2 removes the
  gate after its acceptance and adds the secondary-pages disclosure; W2c adds
  neither the disclosure nor any browser/system-language defaulting.


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

**W4d-2** translates the app-owned Usage v1 Workload/Efficiency presentation,
including column/run-type labels, CLI default (not pinned), comparison/coverage/
withholding/partial/unknown copy, help, statuses, errors/retry/stale states,
scroll hints, ARIA, exact-token titles, counts, units and timestamps. Raw agent,
CLI/model identities, authored values, timezone IDs and diagnostics remain
verbatim. `useUsagePresentation` binds existing i18n context to pure Usage
formatters; it owns no queries, selection, state or effects. The five run types,
six-column tables, null/zero distinctions, selection/refetch recovery and all
server metrics remain the existing Usage v1 contract.

`*_local` window strings already contain org-local wall-clock parts: read those
parts without converting through the viewer timezone. UTC instants use the
response timezone with the central `monthDayClock24` shape (24-hour time in
both locales); `monthShort` supplies localized month names for wall-clock
parts. Malformed local/instant/timezone fallback behavior remains unchanged.
English compact token suffixes retain the canonical K/M shape; Chinese uses
万/亿. Switching en→zh-CN→en re-renders presentation without remounting
controls, losing focus/cohort/Compare, or issuing API requests. Usage is translated; the mounted Assistant dock and conversation controls are
also translated. W5b enables full browser-language defaults. Desktop N0/N1 remain deferred. Ordinary browser evidence uses the narrow `--slice usage` rows in
`web/scripts/w4a-browser-evidence.mjs`, including 390/1440 geometry and
same-origin preference switching. Actual final-head receipts live in task
output/attachments.

### W5a modal and Markdown loading-copy boundary

**W5a copy repair:** the mounted Jobs Run/Reject, Settings Assistant/Capacity/Organization, Tasks Cancel/Revisit/Resolve, Threads Archive/Invite/RemoveParticipant and shared NewThread dialogs pass the existing `common.close` label to their built-in close control. Their actions and focus-return behavior are unchanged. `Markdown` accepts optional `mermaidLoadingLabel`, and `MessageBubble.labels.mermaidLoading` forwards it. KB, Task detail/recall, Threads and both Assistant turn variants supply `common.mermaidLoading`; omitted props retain “Rendering diagram…”. A private Markdown-local context carries only that string to the stable code renderer. Suspense remains per Mermaid block, and changing locale preserves loaded diagrams without another render. Authored Markdown/code and raw Mermaid failure source stay verbatim, including text equal to either loading label. Coverage markers remain inventory; W5a rendering acceptance requires the finite mounted-state/browser evidence. W5a finite mounted coverage is accepted (PR993); W5b enables full browser resolution and bilingual availability copy. Desktop N0/N1 remain deferred. Pending-import assertions live in the isolated Markdown.loading.test.tsx file; Markdown.test.tsx SVG/error cases use an independent lazy-module instance so filters and file order do not depend on releasing another test’s import.
**W5a Work Hours reachability:** the agent-detail reconciliation heading and local edit controls wrap within the page, including long raw agent/team names. The reconciliation and overview roster tables keep every column, value and provenance cell in a localized, named, keyboard-focusable horizontal scroll region; focus the region and use the arrow keys to reach the rightmost columns. Shared Button/AppShell and editor/action semantics are unchanged. The earlier 390px document-width checks did not establish child/control or column reachability: historical clipping captures remain failed evidence. The affected ordinary-build browser case is `web/scripts/w4a-browser-evidence.mjs --slice work-hours-reachability`, checking actual control bounds, readable headings and keyboard access to the final column in en/zh-CN at 390/1440, with mounted editor/draft/focus/navigation preservation. W5a finite coverage was independently reviewed, QA-verified and manager-accepted with PR993; W5b enables full browser locale resolution. Historical evidence keeps its original source/build identity. The tier editor now stacks field rows below the small-screen breakpoint, bounds grid children and field/control groups, wraps resets/day controls and full raw title/provenance/selected-timezone text, and allows the existing dialog to scroll vertically. Desktop retains the row hierarchy. Editor child and text-range bounds must be measured against actual dialog content and viewport after scrolling, with pointer hit testing and a real Tab cycle; page/table bounds alone do not establish editor readability. The affected browser cases include all three tiers, windowed/continuous and impact stages, long raw values, both locales at390/1440, and both-direction node/focus/selection/draft preservation with zero locale HTTP. Actual Save/PUT, reset-null and verbatim422 contracts remain owned by TierEditorDialog.test.tsx and work-hours.i18n.test.tsx; screenshots alone do not prove writes.


W5b responsive Settings preserves the five existing links and their order, labels,
icons and active state. Below640px the links wrap above the panel; at640px and
wider the rail/panel layout remains. Local Preferences min-size/wrapping and
padding keep full disclosure, endonyms and status readable without truncation.
Settings loading/error/data gates, Preferences API independence and user
handlers/state/focus are unchanged.

## 2. Exports

| Module | Contract |
| --- | --- |
| `@/lib/i18n/locale` | `Locale`, `LocaleMode`, `LocaleSource`, `LocaleAuthority`, `LOCALE_STORAGE_KEY`, `SUPPORTED_LOCALES`, `isLocale`, `matchSystemLocale`, `resolveLocale`, `LocalePreferenceAdapter`, `LocaleSnapshot`, `LocaleWriteOutcome`, `browserLocalePreferenceAdapter`, `adapterAuthority`, `readAdapterSnapshot`, `getLocalStorage`, `applyDocumentLocale`, `bootstrapDocumentLocale` |
| `@/lib/i18n/catalog` | `MessageKey`, `MessageValue`, `PluralForms`, `Catalog`, `catalogs`, `PLURAL_FORMS_BY_LOCALE`, `translate`, `renderTranslated`, `lookupMessage`, `resolveMessage`, `selectPluralCategory`, `interpolate`, `extractPlaceholders`, `validateCatalogParity`, `assertCatalogParity` |
| `@/lib/i18n/format` | `formatTokensFor`, `formatCountFor`, `formatDateTimeFor`, `formatDateShapeFor`, `LocaleDateShape` |
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
- **Production selects full mode** at both `main.tsx` and `AppShell`. A valid
  saved choice wins; absent/invalid values follow the existing ordered supported
  browser languages, then English. Startup never writes a preference.
- Resolver/bootstrap/provider API defaults remain preview for foundation fixtures.
  Production storage change/delete/clear uses full resolution without write echo.
- `bootstrapDocumentLocale({ mode: 'full' })` is called from `web/src/main.tsx` before the first
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
test. After W4d-2 exactly twenty namespaces are `translated` (`root-shell`,
`not-found`, `onboarding`, `app-shell`, `help-and-palette`, `settings`,
`dashboard`, `threads`, `tasks`, `jobs`, `health`, `dreams`, `todos`,
`work-hours`, `audit`, `agents`, `skills`, `kb`, `artifacts`, `usage`); every
later slice and route family is still visibly `english-only`, so English
fallback is never mistaken for coverage. The `settings` namespace includes the
(production-enabled since W3b-2) `preferences` token and `PreferencesSection`; the shared
`EligibilityEditorDialog` it lists is owned by Work Hours and is translated by
W4b. The `system-assistant` namespace records both
`AssistantDockHost` and its mounted `ConversationSwitcher`; the help/palette hosts moved to the
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
- `formatDateShapeFor(locale, value, shape, timeZone?)` (W4b) renders a named,
  centrally owned date/time shape (`weekdayDate`, `weekdayMonthDay`,
  `monthDay`, `monthDayYear`, `weekdayLong`, `clock24`, `clock24Seconds`,
  `monthDayTime`, `dateTime`) with an explicit locale. `timeZone` is an IANA
  id; it is omitted only where a surface deliberately shows the viewer's local
  time (Audit row clock, Work Hours wake times, Todo review dates). Features add
  a shape here rather than hand-assembling `Intl` options. In the W4b route
  families (`features/todos`, `features/work-hours-config`, `features/audit`,
  `shared/work-hours`) every visible date/time goes through this module;
  `format.test.ts` scans those directories and fails on any
  `toLocale*String(` or a non-machine-locale `Intl.DateTimeFormat(`. The only
  remaining feature-local `Intl.DateTimeFormat('en-US' | 'en-CA')` calls are
  timezone-conversion `formatToParts` parsing and `<input>` values, which are
  not presentation.
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
renders English with `html.lang=en` (historical W1 preview fixture; production full mode is enabled by W5b). An isolated
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

**W2b onboarding browser evidence.** `web/scripts/w2b-onboarding-browser-evidence.mjs`
drives the ORDINARY `web/dist` bundle (no evidence-only Vite injection, so the
proof runs against the shipping bytes) served next to a synthetic `/api/v1`
stub, and navigates the real `/onboarding` route. Locale switching uses the
supported browser `localStorage` preference (`happyranch.ui.locale`) plus a
same-origin `storage` event — there is no public language selector — and every
page installs and asserts the real `navigator.language`/`navigator.languages`
before app modules. It asserts, in both locales: first-run vs returning routing
and connect-wrapper copy; built-in and custom connect modes; the generated
copy-paste prompt bytes and raw conformance step ids preserved across an
en→zh-CN→en switch while the surrounding step labels re-translate; the create
form's client validation, a mapped daemon error that re-translates across a
switch with no resubmission, an unknown error diagnostic preserved verbatim, and
the success state with the raw slug; the broken-org list with the raw slug/error
verbatim; and the prereq panel with the raw tool name/path/hint verbatim. S4-S6
and S8-S10 each exercise en→zh-CN→en. Immediately before each direction they
open a separate request window, then assert the actual `document.activeElement`,
retained test-only DOM identities where stable, open phase/mode, and the
case-specific value/error/slug/prompt/token/step-id/prereq bytes. Every window
asserts zero `PUT /settings/org`, duplicate `POST /api/v1/orgs`, connect/mint
mutation, and total `/api/` requests; the create cases retain exactly one
original create and built-in waiting retains exactly one original mint. It
captures PNGs across 1440×900 and 390×844 light/dark with CJK-font and
viewport-containment checks; PNGs and `receipt.json` are bound to the head SHA
(exact counts bound to the pushed `receipt.json`). The unit suite retains the
raw-`Error.message` and prompt-byte negatives deterministically.

Frontend readiness map (actual evidence):

| Readiness item | W1 | W2a/W2b/W2c/W3a/W3b-1/W3b-2/W4a-1/W4b/W4c/W4d-1/W4d-2 |
| --- | --- | --- |
| Route-wide Chinese rendering | N/A — no route translated in W1 (manifest marks every namespace `english-only`) | mounted shell + onboarding (W2a/W2b), Settings (W2c), Dashboard/Threads (W3a), Tasks (W3b-1), Jobs (W3b-2), Health/Dreams (W4a-1), Todos/Work Hours/Audit (W4b), Agents/Skills (W4c) and KB/Artifacts (W4d-1); Usage presentation (W4d-2); the mounted Assistant dock and conversation controls are translated with component and ordinary-build browser evidence |
| Public language selector | N/A — W3b-2 | Historical W3b-2: Settings ▸ Preferences mounted in ordinary builds; Vitest proves sub-nav + direct URL without a flag, unset-on-Chinese-browser stays English, disclosure visible, zh-CN switch keeps nodes/focus with zero requests; browser: `web/scripts/w3b-jobs-browser-evidence.mjs` against the ordinary dist |
| Browser two-tab live evidence | covered by unit/integration storage-event tests AND captured same-origin two-tab change/delete/clear/no-echo browser evidence at the head SHA | W1 behavior retained (no native adapter added) |
| Bilingual foundation browser capture | captured: real-browser story screenshots for saved-en-in-Chinese-env, saved `zh-CN` (CJK font glyphs) and preview-unset English | W2a/W2b add real-app shell/dialog and onboarding captures across en/zh, 1440x900/390x844 and light/dark (exact count bound to the pushed `receipt.json`) |
| Production startup first-paint | captured: real `main.tsx`/`createBrowserRouter` startup with synthetic API stub; first COMMITTED bilingual consumer text and `<html lang>` asserted together | W2a reads the ACTUAL first committed Sidebar/AppBar DOM (frozen on first connection, never overwritten by a later correction) with `<html lang>` and the real navigator read-back; a causal `I18N_W2A_EVIDENCE=negative` control proves the same predicate rejects an initially-wrong shell |
| Mounted-shell switching state | N/A (foundation) | captured: help non-default tab (S16), AddOrg typed slug + mapped error (S12 wide-light; S17 zh-narrow-light/en-narrow-dark/zh-wide-dark) and palette query + non-default selected row (S13 wide-light; S18 zh-narrow-light/en-narrow-dark/zh-wide-dark) preserved across storage-path locale switches with the actual `document.activeElement`, retained DOM node identity, open state and selection/value observed before AND after each direction; each help/AddOrg switch window asserts no `PUT /settings/org` and no `POST /api/v1/orgs`, and each palette switch window asserts zero `/api/` requests (cache-only), per direction |
| Onboarding switching state (W2b) | N/A | captured: S4 mapped error, S5 raw `API 500`, S6 success, S8 built-in waiting, S9 custom form and S10 prereq/create state each run en→zh-CN→en with actual focus, stable test-only node identity where applicable, open phase/mode and state-specific raw bytes retained; a separate per-direction window proves zero settings/org/connect/mint mutation and zero `/api/` requests; PNGs and `receipt.json` are bound to the head SHA |
| Settings + Preferences (W2c) | N/A | Historical phase evidence (defaults superseded by W5b): Vitest: since W3b-2 no gate — sub-nav lists Preferences and a direct URL renders it (unit and real `AppRoutes`), index/redirect/back/forward, Preferences under settings API loading/error/empty/ok, both switch directions with radio/sub-nav node identity + focus + route, zero requests in the switch window, keyboard Space selection, honest durable/failed persistence, storage-event sync without write-back, per-section zh-CN copy with raw detail verbatim, an already-visible Work Hours banner and executor product fallbacks re-translating in both directions with no resave/remount/request while raw diagnostics (incl. catalog-equal and empty) stay verbatim; browser: `web/scripts/w2c-preferences-browser-evidence.mjs` against two ordinary builds of the head (case A: the ordinary dist mounts Preferences and an unset preference stays English; cases B–I on `--preview-dist`, now flag-free) plus a `--defect-dist` causal negative (Organization banner and Executors raw-diagnostic cases included; receipt/PNG hashes bound to the pushed head) |
| Dashboard + Threads (W3a) | N/A | Historical phase evidence (defaults superseded by W5b): Vitest: Dashboard loading/error+Retry/first-run/populated in both locales with entity values verbatim; Threads list/detail states, composer and NewThreadDialog draft/attachment/focus/node identity across both switch directions with zero requests then exactly one create/reply; dialogs open across switches; mapped errors re-translate while raw (catalog-equal/empty/`HTTP <status>`) diagnostics stay verbatim; browser: `web/scripts/w3a-core-browser-evidence.mjs` against the ordinary dist only (gate case G: the bundle mounts Preferences, `/settings/preferences` renders, unset on a Chinese navigator stays English) with receipt/PNG hashes bound to the pushed head |
| Tasks (W3b-1) | N/A | Vitest (`features/tasks/tasks.i18n.test.tsx`, `TaskEventsLog.test.tsx`, `fanout.test.ts`): routed list populated/grouping/filter/empty/error and detail header/lineage/rail/execution-status/fan-out/chain in both locales with authored and machine values verbatim; same row/heading/dialog/field nodes, draft value and focus across both switch directions with zero requests; mapped dialog errors and the Revisit validation re-translate while unmapped (incl. catalog-equal) codes stay verbatim; StatusBadge English default; browser: `web/scripts/w3b-tasks-browser-evidence.mjs` against the ordinary dist (Tasks list + detail, en/zh-CN, 1440/390, one dialog-draft switch check, ordinary-bundle Preferences markers present since W3b-2) |
| Jobs (W3b-2) | N/A | Historical phase evidence (defaults superseded by W5b): Vitest (`features/jobs/jobs.i18n.test.tsx`): routed list populated/empty/error and detail pending/completed/load-error in both locales with daemon values verbatim; same card/button/field nodes, Run/Reject draft value and focus across both switch directions with zero requests; the F1 diagnostic boundary through the routed Run dialog (recognized code re-translates in place, unknown code and code-less string detail verbatim, empty code / blank detail fall back), Reject mapped code and Stop mapped code; browser: `web/scripts/w3b-jobs-browser-evidence.mjs` against the ordinary dist (Jobs list + detail + Run dialog draft switch, Preferences unset-English + disclosure + zero-request switch) |
| Health + Dreams (W4a-1) | N/A | Vitest (`features/health/health.i18n.test.tsx`, `features/dreams/dreams.i18n.test.tsx`; `HealthPage.test.tsx`/`DreamsPage.test.tsx` now assert through the catalog): routed Health populated/empty-history/live-error/history-error and Dreams feed populated/empty/error + rail + detail drawer in both locales with daemon values verbatim; same loop-cell/window-button/card/drawer/Accept nodes and focus across both switch directions with zero requests; the F1 boundary through the routed Accept action (recognized `candidate_*` code re-translates in place, unknown code and code-less string detail verbatim, empty code falls back to the localized failure copy); browser: `web/scripts/w4a-browser-evidence.mjs` against the ordinary dist (G ordinary-bundle zh-CN copy, V health/dreams/dream-drawer en+zh-CN at 1440/390, S drawer Accept-focus switch with zero /api requests) |
| Todos + Work Hours + Audit (W4b) | N/A | Vitest (`features/todos/todos.i18n.test.tsx`, `features/work-hours-config/work-hours.i18n.test.tsx`, `features/audit/audit.i18n.test.tsx`; `TodosPage.test.tsx`, the Work Hours page/dialog tests and the Audit page/narrative/filter tests now assert through the catalog): routed Todos list/detail, Work Hours overview/wakes/agent detail and Audit timeline/filters in both locales with daemon values (agent names, schedule/task ids, actions, timezones, raw payload values) verbatim; recurrence/timezone and every visible date/time through `lib/i18n/format.ts` (`formatDateShapeFor`, guarded by the `format.test.ts` source scan); count-bearing Todos/Audit copy as plural objects (English `one` + `other`, Chinese `other`) selected by a numeric `count`; TierEditorDialog and EligibilityEditorDialog pass `closeLabel={t('common.close')}` so the close control is `关闭` under zh-CN; narrative sentences from catalog templates with interpolation; Todo EditDialog, TierEditorDialog and the shared EligibilityEditorDialog keep draft, nodes and focus across both switch directions with zero requests; the F1 boundary through `features/<x>/strings.ts` classifiers; browser: `web/scripts/w4a-browser-evidence.mjs` route-table rows (G ordinary-bundle zh-CN copy, V todos/work-hours/audit en+zh-CN at 1440/390, with todos also asserting every row card holds its rows (`scrollWidth <= clientWidth`; the TodoRow metadata group wraps instead of clipping), S Todo EditDialog + TierEditor + EligibilityEditor draft/focus switch with zero /api requests) |
| Agents + Skills (W4c) | N/A | Vitest (`features/agents/agents.i18n.test.tsx`, `features/skills/skills.i18n.test.tsx`; the existing Agents/Skills page, dialog and helper tests now assert through the catalog): routed agents list/detail/team-escalation-policy and every `skills*` route in both locales with user/daemon values (agent names, roles and team identifiers without translation or title-casing, policy bodies, contract ids, digests, skill names/slugs/descriptions/SKILL.md bodies, versions, provenance) verbatim; every visible date/time through named `lib/i18n/format.ts` shapes, including policy release/activation history and purged custom-skill completion via `dateTime` (en/zh-CN rendered-state regressions plus the direct-formatting source scan); count-bearing copy as plural objects selected by a numeric `count`; in-scope dialogs pass `closeLabel={t('common.close')}` (`关闭` under zh-CN); the F1 boundary through `classifyAgentError`/`classifySkillError` (`features/<x>/strings.ts`); browser: `web/scripts/w4a-browser-evidence.mjs` route-table rows (G, V agents/skills routes en+zh-CN at 1440/390 with a non-vacuous container `scrollWidth <= clientWidth` check, exact daemon roster-role bytes, and below-`md` main min-height `0px` / unchanged `md`+ `auto` with stacked/row layout respectively; S in-scope dialogs keep draft, nodes and focus with zero /api requests) |
| Native Mac persistence receipt | N/A — N0/N1 (Linux host; not claimed) | NOT RUN — N0/N1 still open |

## 10. Exclusions

No new dependency, daemon/API/schema/auth/permission/transport change, theme or
draft migration, native chrome, CLI/manual translation, route-family
translation campaign beyond W3a/W3b-1/W3b-2/W4a-1/W4b/W4c/W4d-1/W4d-2 (W4a-1 translated Health
and Dreams; W4b translated Todos, Work Hours and Audit; W4c translated Agents
and Skills; W4d-1 translates KB and Artifacts; W4d-2 translates Usage presentation; the mounted Assistant dock and conversation controls are translated), deployment, or caller migration of display
formatters beyond the translated shell, onboarding and the W3a Dashboard/Threads,
W3b-1 Tasks, W3b-2 Jobs, W4a-1 Health/Dreams, W4b Todos/Work Hours/Audit, W4c Agents/Skills W4d-1 KB/Artifacts, W4d-2 Usage route families and mounted Assistant presentation. Existing query/data/auth
bootstrap
semantics are preserved; locale switching issues no `PUT /settings/org` and no
`POST /api/v1/orgs`, and the command palette's cache-only switch issues no
`/api/` request in the measured window. Onboarding preserves user-entered
slugs, the slug regex, registered tool names/paths, broken-org raw errors and
the generated copy-paste CLI prompt bytes verbatim; the shared ConnectFlow is
the single implementation for onboarding and Settings ▸ Executors. The
`settings` namespace is `translated` (W2c: ordinary Settings chrome and the
Assistant/Organization/Executors/Capacity sections; the Work Hours-owned
`EligibilityEditorDialog` stayed English until W4 and is translated by W4b); the Preferences
route/selector is mounted in ordinary builds since W3b-2.

## Mounted System Assistant copy (THR-118)

The mounted `AssistantDockHost` and its `ConversationSwitcher` resolve app-owned visible and accessible copy through `assistantDock.*` in en/zh-CN. This includes configuration/loading/empty states, header/composer/key hints, conversation list/actions/rename/delete confirmation, tool activity and fallback speaker labels. The existing MessageBubble speaker/timestamp and TypingBubble caption/ariaLabel overrides are reused; timestamps retain the viewer-local full date/time display and elapsed values retain the existing seconds/minutes calculation.

Errors capture provenance when they arrive: `{ key }` is an app fallback, `{ raw }` is daemon detail/message, and `{ connectionDetail }` preserves `String(caughtValue)` under a localized wrapper. Rendering resolves keys with the current locale, including an already-visible fallback. A raw empty value or one equal to an English catalog string remains raw. Supplied executor/tool names, conversation titles/IDs, authored prompts/replies/Markdown and timestamps as data remain verbatim. Missing tool-name presentation has its own provenance flag; tool matching keeps the existing raw/default name semantics.

Locale changes do not key/remount the dock or enter its connection-effect dependencies. They preserve the active conversation, history/inflight state, composer/rename draft, selection/focus and open delete confirmation, and issue no HTTP/session/socket activity. Existing A-mode parsing/order/hydration, query/polling keys, activation/new/rename/delete callbacks, reconnect triggers, optimistic send/trim/clear semantics, hotkeys, focus trap/restore and SPA navigation remain unchanged. Help and Command Palette chrome already use the shared locale context and retain their current interaction ownership.

Ordinary-build evidence uses `web/scripts/w4a-browser-evidence.mjs --slice assistant` and the narrow `assistant-dock-browser-cases.mjs` fixture/cases: real shipping HTTP/WS seams, server request/socket ledger, mounted-node/focus assertions and 390/1440 captures. The coverage inventory includes both actual mounted consumers. The `system-assistant` marker is translated after complete mounted-copy coverage is established by component regressions and ordinary-build browser evidence; catalog binding alone does not prove coverage. W5a accepted the finite mounted audit and resolved the Settings Organization disable-confirm close label (PR993). W5b enables full browser-language defaults while preserving the state/transport contract. Desktop restart persistence remains deferred; deployment/live acceptance is separate.
