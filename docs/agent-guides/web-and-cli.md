# Web And CLI

## Daemon-managed workspace cleanup

`workspace_cleanup.reclamation_actions_enabled` is an internal, strict boolean
configuration key that defaults to `false`; it creates no Web or CLI setting.
When enabled, only the pre-agent cleanup hook may act, and only on a
third-or-later cleanup ordinal whose scheduler-created preclaim owner is
assigned to a registered in-memory `TeamsRegistry` agent and reconciles
to the invocation's initial successful claim (the first two runs stay
report-only), after its current-owner, bounded provenance, one-second deadline,
and at-most-23 read/load admissions, with no refill or recovery. It makes at
most five best-effort consumer calls. A fresh `false` value stops a later
admission but cannot preempt an already admitted call. Each attempt records the
owner `workspace_cleanup_reclamation_attempt` audit before the prompt carries the
known facts, including the literal `after` remainder or `null`. A `None`
consumer result is reported literally as
`refused_or_unavailable`; known partial/failure facts are retained, the
transported remainder is exactly the returned `after` accounting or `null`, and
publication failure stops later calls. The ordinary agent completion summary
remains the normal callback/CLI result surface.

The cleanup contract itself is the ONE shared `workspace-cleanup` TASK system
skill (`requires_repo=false`), reachable from both workspace skill roots. Manual
dispatch requires the exact first line `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN
(manual-dispatch)`; an unmarked manual request is inventory-only. Its bundled
read-only `scripts/check_path_use.py` returns only `clear_observation`,
`blocked`, or `unknown`, using the approved THR-259 seq171/seq185 rule (exact
process name AND bounded cgroup role for fixed daemons; any other unreadable
same-user process is `unknown`).

Cleanup history reads use the backward-compatible `tasks --agent --all-pages
--json` and `audit --all-pages --json` surfaces to exhaust candidate-specific
keyset pages. Scanner execution is job-only and fail-closed on any non-exact,
stale, spoofed, truncated, or incomplete structured receipt. `jobs submit
--json` returns the authenticated submission identity; `jobs show --json` and
`jobs output --json` require the current task/session and return the same closed
receipt containing stored execution identity and complete output accounting.
Human-readable `happyranch jobs show` keeps its existing layout and adds a
`Reason:` line only when the existing job `reason` field is non-null; graceful
daemon-shutdown kills therefore show `failed`, `daemon_shutdown`, and the real
exit code without an API-field change.
An owning origin task branch whose head equals or descends from the candidate,
an owning-task merged PR, and a containing any-task merged PR can preserve a
clean candidate in addition to the accepted durable ref. An existing owning
branch is authoritative: non-containment or failed containment evidence refuses
without merged-PR fallback. The any-task route
uses complete stable double-read discovery, then separately confirms
merged/default-branch state and candidate-to-PR-head containment; discovery
alone and other-task unmerged PRs never count, while owning-branch unmerged
evidence still refuses. Merged integration does not promise original commit
topology. Dirty whole worktrees are never removed, while a literal root
dependency cache can qualify under the separate 24-hour cache gates without
changing tracked source bytes or Git status. The removable worktree must be at
its owning primary checkout's exact registered `.claude/worktrees/<TASK>` path.
The literal root and descendants are walked without following symlinks before
and at action time; nested mounts, cross-device or foreign-owned entries,
protected descendants, incomplete evidence, and identity drift refuse. A
success receipt requires measured root-plus-descendant bytes, actual candidate
absence, and unchanged protected-path identities.

The independent forward-only terminal task-worktree hook has no Web/CLI or
configuration surface. Its final process gate loads this same bundled scanner
by explicit file path inside the existing task worker thread: root-owned
processes are out of scope, only exact name-plus-expected-cgroup helpers are
exempt, any other unreadable same-user process is uncertain, and a positive
reference preserves. The hook retains one five-second total deadline and one
literal non-force removal attempt.

## Web UI

### Dashboard projection

`GET /api/v1/orgs/{slug}/dashboard/summary` reads the per-org in-memory
last-known-good projection. `runtime/orchestrator/dashboard_projection.py`
owns coalesced background refresh and atomic `dashboard_projection.json`
publication; the HTTP route never composes the summary under its request lock.
A valid persisted projection serves after restart. Without one, the route
returns 503 until background warm-up succeeds. A refresh failure retains prior
data; `generated_at` describes that data's age and `server_now` is response time.
Database lock wait/hold warnings identify slow operations without changing the
schema. Dashboard/projection tests cover these behaviors.

The SPA supports mutations, including task cancellation/revisit and Settings.
Use `web/src/routes.tsx`, API functions, and the OpenAPI snapshot for the current
surface. The daemon defaults to loopback; remote access uses the connector.

### Internationalization (W1 foundation + W2a shell + W2b onboarding + W2c Settings + W3a Dashboard/Threads + W3b-1 Tasks + W3b-2 Jobs/preview + W4a-1 Health/Dreams + W4b Todos/Work Hours/Audit + W4c Agents/Skills)

The web console has a first-party, typed English/Simplified-Chinese contract in
`web/src/lib/i18n/` (`locale`, `catalog`, `format`, `coverage`) with the
`I18nProvider` in `web/src/hooks/i18n.tsx`. The initial locale is resolved
synchronously before the first React text and applied to `<html lang>`; the one
resolution is handed through `App`/`AppShell` to the provider so it is never
resolved twice. Production runs in preview mode, so an unset
`happyranch.ui.locale` preference renders English regardless of the environment.
Storage failures degrade to an in-memory session with an honest persistence
result, same-origin `storage` events sync other tabs without write loops, and
sequenced acknowledgements prevent a superseded write from reporting durable
success. Adapter `authority` declares ownership: browser authority keeps
`localStorage` and cross-tab events authoritative, while a native adapter's
injected snapshot wins. `web/src/lib/format.ts` remains the canonical display
formatter; `web/src/lib/i18n/format.ts` adds explicit-locale interfaces that
delegate to it, and `formatCount` takes an optional locale (omitting it keeps
the legacy host-default behaviour). An unexpected catalog gap renders the
English message with English plural grammar, never a raw key.

**W2a** translated the mounted shell — AppBar page titles and controls, Sidebar
navigation/aria/org-switcher/account copy, the root loading and NotFound
fallback, the ErrorBoundary fallback copy, the AddOrgDialog, and the shared
help/palette presentation — and added CJK-capable SYSTEM font fallbacks to
`web/src/design-system/tokens/tokens.css` (no webfont download or dependency).
AddOrgDialog, help and palette also localize the built-in dialog close control:
`DialogContent` gained a backward-compatible optional `closeLabel` prop
(defaulting to the legacy English `Close`) that callers pass from the catalog,
and the pattern/primitive layers remain prop-driven with no locale hook import.
AddOrgDialog stores the product-owned error identity plus the submitted slug and
re-translates at render time, so an already-visible mapped error follows a
locale switch without resubmission while unknown external daemon detail stays
verbatim.

**W2b** translated the onboarding route (`/onboarding`): `OnboardingPage`
(first-run vs returning welcome, create/creating/success, the read-only
broken-org list and the executor-prereq panel), the `ConnectRuntimeStep`
wrapper chrome, and the shared `src/shared/connect/ConnectFlow.tsx` bodies
(built-in/custom modes, waiting/committing/connected, retryable + terminal
failure, clear/recovery, copy feedback and aria text). The add-org error
classifier/renderer is now the single shared
`src/lib/addOrgError.ts` consumed by both AddOrgDialog and the
onboarding create step. Because `ConnectFlow` is shared with Settings ▸
Executors, translating it also localizes that connect body, but the `settings`
namespace stays `english-only` in the manifest (the surrounding Settings chrome
and sections are not translated) and Settings is not claimed as translated.
User-entered slugs, registered tool names/paths, the slug regex, broken-org raw
errors and the generated copy-paste CLI prompt (raw step ids, tokens, routes)
stay byte-for-byte verbatim; mapped categories re-translate on a locale switch
with no resubmission.

**W2c** translated the Settings surface (`/orgs/:slug/settings/*`): the page
header, sub-nav, API loading/error copy and panel headings, plus the Assistant,
Organization, Executors (registered list, custom profiles, binary paths) and
Capacity section bodies. Raw daemon errors, identifiers, executor and
agent names, config keys, paths, commands and every capacity number stay
verbatim; only product-owned surrounding copy is localized. The shared
`EligibilityEditorDialog` (mounted by Organization but owned by Work Hours)
stayed English until W4; W4b translates it. W2c also builds the client-only **Settings ▸
Preferences** language selector (`sections/PreferencesSection.tsx`: English /
简体中文 endonym radios with their own `lang`, immediate apply through the W1
`setLocale`, `<html lang>` update, honest pending/saved/failed status) and
splits the Settings shell so the `preferences` route renders OUTSIDE the
`useSettings` loading/error/data gate; every other panel keeps that gate
unchanged. Choosing a language never writes org settings or issues any API
request. Until W3b-2 the selector was closed in production by a
`VITE_ENABLE_I18N_PREFERENCES` build gate (`languagePreferenceGate.ts`, now
removed — see W3b-2 below). The W2c browser harness
(`web/scripts/w2c-preferences-browser-evidence.mjs`) originally built a separate
preview dist with that flag; since W3b-2 both of its dists are ordinary builds
and its case A asserts the mounted selector (route renders, unset preference
stays English). It also proves that state-held Settings messages follow a locale
switch (the Organization Work Hours banner re-translates in place) while raw
daemon diagnostics stay verbatim, with causal negatives for a pre-translated
banner (`--defect-dist`) and a translated diagnostic. There is no browser/system
language defaulting (unset/invalid stays English).

**W3a** translated the mounted Dashboard and Threads route families (`features/dashboard/**`, `features/threads/**` list/detail/composer/strips/dialogs and the shared `shared/threads/NewThreadDialog.tsx` it mounts); pure design-system patterns (Composer, ThreadHeader, InboxRow, StatValue, CrescentMoonBadge, RecipientsInput, MentionTextarea, …) take optional localized label props with English defaults, so their other callers are unchanged. Thread errors are held as locale-neutral `ThreadErrorView` descriptors (`lib/threadErrors.ts`: mapped catalog key/params, or `raw` text rendered byte-for-byte even when empty or equal to a catalog string) and rendered at render time. Authored thread titles, message Markdown, names, IDs, filenames/hrefs, raw delivery payloads and machine values stay verbatim; a locale switch keeps drafts, attachments, selection, open dialogs and focus and issues no request. W3a browser evidence runs `scripts/w3a-core-browser-evidence.mjs` against the ORDINARY dist only (storage-event switching, no in-app instrumentation); since W3b-2 its gate case G asserts the ordinary bundle contains the Preferences selector, `/settings/preferences` renders it, and an unset preference on a Chinese navigator stays English (the former preview-dist positive control is gone with the flag).

**W3b-1** translated the mounted Tasks route family (`tasks`, `tasks/:task_id`: `features/tasks/**` list/detail panes, filters, group/status/rollup presentation, fan-out band, chain/lineage timeline, recall tree, activity log, property rail and execution-status card, loading/empty/error states, and the owned Cancel/Revisit/ResolveEscalation dialogs). The shared `StatusBadge` takes an optional localized `waitingLabels` prop with an English default, so its Jobs and TaskCard callers are unchanged. Dialog errors are held as locale-neutral `TaskErrorView` descriptors (`features/tasks/strings.ts`: mapped catalog key; an unmapped daemon code or, with no code, a non-empty string diagnostic rendered verbatim; otherwise the localized fallback) and the Revisit validation message is a state-held key, so both re-translate in place. Briefs, summaries, notes, agent names, task/thread/job IDs, status/block-kind/verdict/reason machine values, unknown escalation flavors and work-status states (the daemon label), raw event actions/payloads, hrefs and shortcut keys stay verbatim; group and lineage React keys stay locale-neutral, so a locale switch keeps rows, selection, open dialogs, typed drafts and focus and issues no request. W3b-1 browser evidence runs `scripts/w3b-tasks-browser-evidence.mjs` against the ORDINARY dist.

**W3b-2** translated the mounted Jobs route family (`jobs`, `jobs/:job_id`: `features/jobs/**` list chrome, needs-you callout, status groups, columns and pending relative age; detail states, header actions, command card, If-approved cascade, gated notice, property rail and output panel; the owned Run/Reject dialogs). Daemon values stay verbatim: job/task IDs, titles, script text, rationale, agent names, interpreter/cwd values, job status tokens and `exit <code>`, exit codes, stdout/stderr and their stream names, the live `[done]` log line and rejection/failure reasons. Run/Reject/Stop errors are locale-neutral `JobErrorView` descriptors (`features/jobs/strings.ts` `classifyJobError`, the same F1 boundary as `classifyTaskError`: recognized code -> catalog key; unknown non-empty code -> raw; no/empty code with a non-empty string diagnostic -> verbatim; otherwise the localized fallback), replacing the former English `Error <status>: API <status> (<code>)` text. W3b-2 also **enables the opt-in language preview**: the `languagePreferenceGate.ts` / `VITE_ENABLE_I18N_PREFERENCES` gate is removed, so Settings ▸ Preferences ▸ Language is mounted in ordinary production builds. An unset preference stays English (production resolves in `preview` mode and never reads the browser language), and the selector discloses that secondary, not-yet-translated pages may still appear in English (`settings.preferences.coverageDisclosure`). Browser evidence runs `scripts/w3b-jobs-browser-evidence.mjs` against the ORDINARY dist.

**W4a-1** translated the mounted Runtime Health and Dreams routes (`features/health/HealthPage.tsx`: header, history-window toggle, stat cards, uptime/relative-age units, loop and HTTP-latency tables, trends; `features/dreams/**`: header eyebrow plural, feed, status pills, counts, quiet state, overview rail and the dream detail drawer with its candidate review gate). Daemon values (loop names, route templates, dream IDs, agent names, local dates, summaries, transcripts, error text, candidate title/slug/topic/rationale/body, KB slugs, unknown status tokens) stay verbatim. Accept/Dismiss errors are `DreamErrorView` descriptors (`features/dreams/strings.ts` `classifyDreamError`, the same F1 boundary as `classifyJobError`); `DREAM_STRINGS` is replaced by `dreams.*` catalog keys. Browser evidence: `scripts/w4a-browser-evidence.mjs` against the ORDINARY dist; its `API_ROUTES`/`VIEW_ROUTES`/`SWITCH_ROUTES` tables are extended by later W4 slices (W4c Agents/Skills and W4d-1 KB/Artifacts shipped; Usage presentation W4d-2).

**W4b** translated the mounted Todos (`features/todos/**`: list, detail, status pills, rows, recurrence/timezone presentation, Confirm/Edit dialogs), Work Hours (`features/work-hours-config/**`: overview, wakes, agent detail, TierEditorDialog; plus the Work Hours-owned `shared/work-hours/EligibilityEditorDialog.tsx` that Settings ▸ Organization mounts and its `ErrorPanel`) and Audit (`features/audit/**`: page, timeline, filters and the narrative, whose sentences are catalog templates with interpolation) route families. Agent names, task/schedule IDs, actions, timezones, cron/recurrence values and raw payload/error values stay verbatim; every visible date/time goes through `lib/i18n/format.ts` (`formatDateShapeFor` named shapes; feature-local `Intl.DateTimeFormat('en-US' | 'en-CA')` remains only for timezone-conversion parsing and `<input>` values, enforced by a `format.test.ts` source scan). Count-bearing Todos/Audit copy uses per-locale plural objects selected by a numeric `count`, and the two Work Hours dialogs pass `closeLabel={t('common.close')}`. Error sites use the same F1 boundary through feature-local `strings.ts` classifiers. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

**W4c** translated the mounted Agents (`features/agents/**`: roster list, agent detail pane/drawer, pending enrollments, AddAgentDialog, TeamEscalationPolicyPage/Card) and Skills (`features/skills/**`: catalog, validation, skill detail + assignment panel, custom-skill list/create/detail) route families. User/daemon values (agent names, roles and team identifiers without translation or title-casing, team-policy bodies, contract ids, digests, skill names/slugs/descriptions/SKILL.md bodies, versions, provenance values) stay verbatim; visible dates/times, including policy release/activation history and custom-skill purge completion, use named shapes in `lib/i18n/format.ts` (`dateTime` for those timestamps; en/zh-CN rendered-state regressions complement the source scan, which rejects direct locale-formatting calls), count-bearing copy uses plural objects, in-scope dialogs pass `closeLabel={t('common.close')}`, and error sites use the F1 boundary through `classifyAgentError` / `classifySkillError`. Below `md` the Agents roster stacks above the detail pane (height-capped, internally scrolling) instead of a fixed 244px rail, so the detail is not squeezed at 390px. The detail main pane uses `max-md:min-h-0` only below `md`; at `md` and up its base computed min-height remains `auto`. Browser evidence: route-table rows added to `scripts/w4a-browser-evidence.mjs`.

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
`artifacts` coverage is translated; `usage` is translated by W4d-2; `system-assistant` is translated by the mounted dock/conversation slice. Preview remains enabled with unset
English and secondary-page disclosure; W5 and native N0/N1 remain later work.
Browser evidence adds representative KB list/detail/candidates and artifact
list/folder/upload rows to `web/scripts/w4a-browser-evidence.mjs`, plus one
upload filename/selected File/focus/control-preservation switch in both directions
with zero requests. Final-head evidence is recorded in the task handoff.

The mounted Assistant dock and conversation controls are translated in en/zh-CN.
The opt-in preview selector retains its secondary-page disclosure until the W5
audit of all mounted routes, dialogs and accessibility copy. Native preference persistence is N0/N1; full-mode automatic
environment detection is implemented and unit-tested but not enabled until W5.
`web/src/lib/i18n/coverage.ts` marks exactly the W2a/W2b/W2c/W3a/W3b-1/W3b-2/W4a-1/W4b/W4c/W4d-1/W4d-2/Assistant-migrated namespaces
(`root-shell`, `not-found`, `app-shell`, `help-and-palette`, `onboarding`,
`settings`, `dashboard`, `threads`, `tasks`, `jobs`, `health`, `dreams`, `todos`, `work-hours`, `audit`, `agents`, `skills`, `kb`, `artifacts`, `usage`, `system-assistant`) `translated` and every other mounted route namespace `english-only` (copy-free
redirects `not-applicable`), listing the actual mounted dialogs, so English
fallback is never mistaken for coverage. Foundation browser evidence (isolated
Storybook probe + the real `main.tsx` startup in headless Chrome) runs via
`web/scripts/i18n-browser-evidence.mjs`; W2a shell evidence runs via
`web/scripts/w2a-shell-browser-evidence.mjs` under an independent
`I18N_W2A_EVIDENCE`-gated, test-only build injection. It reads the actual first
committed Sidebar/AppBar DOM plus `<html lang>` and the real navigator
read-back, retains a causal negative control (`I18N_W2A_EVIDENCE=negative`:
wrong first shell, later corrected, must be rejected by the same predicate), and
covers both locales across the observed 1440x900/390x844 light/dark
combinations for root loading/no-org/NotFound/help (S11/S16)/AddOrg
(S12/S17)/error/dormant-palette. In both switch directions it records the actual
`document.activeElement`, retained DOM node identity, open state and
selection/value for the help non-default tab (S16), the AddOrg typed slug +
mapped error (S12 wide-light; S17 zh-narrow-light/en-narrow-dark/zh-wide-dark)
and the palette query + non-default selected row (S13 wide-light; S18
zh-narrow-light/en-narrow-dark/zh-wide-dark), and asserts each switch window
issues no `PUT /settings/org` and no `POST /api/v1/orgs` (the palette windows
also assert zero `/api/` requests, cache-only, per direction). It exercises the
palette's localized X close control (S19) with native Enter/Space/Escape in both
locales with populated and empty results (closes once, zero selection, unchanged
pathname) while the search-input and non-default-row Enter still select once, and
adds CJK-font and viewport-containment checks. Positive receipt: 375/375
assertions, 30 PNGs. W2b onboarding evidence runs via
`web/scripts/w2b-onboarding-browser-evidence.mjs` against the ORDINARY
`web/dist` bundle and a synthetic `/api/v1` stub, driving the real `/onboarding`
route with NO evidence-only bundle instrumentation: locale switches go through
the supported browser `localStorage` preference plus a same-origin `storage`
event (there is no public selector), so the harness proves the shipping bundle
already satisfies the acceptance predicate. It covers first-run/returning
routing, built-in/custom connect modes, the waiting/committed prompt bytes
preserved across en→zh-CN→en, mapped-vs-raw error handling, success, the
broken-org list and the prereq panel. For S4-S6 and S8-S10, each en→zh-CN and
zh-CN→en transition records the actual `document.activeElement`, retained
test-only DOM identity where stable, open phase/mode and state-specific raw
values before and after. Every immediately scoped transition window asserts no
`PUT /settings/org`, duplicate `POST /api/v1/orgs`, connect/mint mutation, or
other `/api/` request; S4-S6 retain exactly one original create and S8 retains
exactly one original mint. The exact assertion and PNG counts are bound to the
pushed `receipt.json`.
Current contract:
`docs/superpowers/specs/2026-09-20-web-i18n-design.md`.

### Web contract and navigation

Layer rules, boundary rules, and agent-callback omissions live in `web/ARCHITECTURE.md`. Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

Every browser-callable daemon route maps to one TypeScript function in `web/src/lib/api/`. Two paired tests enforce this:

- Python: `tests/contract/test_openapi_snapshot.py` pins OpenAPI to `tests/contract/openapi.json`. Regenerate intentional changes with `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- TypeScript: `web/src/test/openapi-coverage.test.ts` asserts every documented path is either included with a TS mirror or excluded with justification.

### Tasks list

The Tasks heading, grouping controls, inline filters and root rows share one
scroll owner. Status, Agent and Thread grouping use loaded matching roots;
counts do not claim a server total or count subtasks. Status grouping preserves
all seven lifecycle groups in display order: Waiting on you, In progress,
Pending, Failed, Completed, Cancelled, Resolved. Completed excludes superseded,
which appears in Resolved; display order does not change severity rollups or
filter-option order. Resting list rows and their lineage use the existing raised
section surface (white in light mode, semantic raised override in dark mode),
with distinct hover styling. Only superseded rows are dimmed in every grouping.
Tasks opts into blue in-progress and amber escalated badges and its own AppBar metrics; other
surfaces retain their defaults.

Filter supports status and exact assigned-agent name. Apply submits both drafts;
closing the panel leaves applied values unchanged. Clear omits both parameters.
An empty agent means no agent filter, not an unassigned-only request. Applied
values remain visible, including for successful empty results. A new org/filter
request starts without another context's cursor; returning to a cached context
retains its own pages. Local recovery ownership resets on context changes.
`Waiting on you` uses a separate root traversal with exact
`status=escalated`; it is never derived from loaded ordinary pages or a
severity rollup. Its rows are root-only and own the presentation when an
ordinary page contains the same task id. While its cursor remains, wording is
non-exact (`50+ waiting on you`); only an exhausted traversal may show an
exact count. The ordinary cursor order is unchanged.
When the attention traversal has rows, they render as the FIRST group inside the
shared list shell — the same `tasks-group` wrapper, group heading (attention
dot + `Waiting on you`) and raised rows-card as every status group — directly
after the task column header and above the status groups; there is no separate
outer padded/inset card. When there are no escalated roots (and attention is
not loading or errored), no `Waiting on you` group, heading or empty card is
rendered. If attention has no rows while its traversal loads or errors, the
ordinary list remains usable and the attention loading/error state renders
separately; a non-empty escalated group can coexist with an ordinary initial
loading/error state without hiding the shared list shell, and a successful
empty ordinary result must not claim `No tasks` while escalated rows are
visible.
Ordinary status/agent filters remain their established independent traversal;
they do not alter the separate attention query. Initial attention failures make
no count claim and show Retry. A failed attention refresh or continuation keeps
the deduplicated loaded rows and their honest current count visible with a
stale/error warning; continuation Retry resumes the failed attention cursor,
while ordinary pagination retains its own separate page Retry. Waiting rows
share the ordinary responsive row treatment because they live under the shared
list shell; its page-local load-more and Retry controls wrap within the group at
narrow widths; shared button behavior is unchanged.
Initial ordinary failures show an
explicit Retry; failed ordinary refreshes retain cached rows and a stale warning;
failed ordinary pagination retains loaded rows and requires its separate page Retry.
Manual refresh retries the active context's loaded pages. Subtask severity
rollups are count-free and reflect the worst **current** status of the root's
`parent_task_id` subtree; a historical FAILED descendant whose same-parent
revisit lineage leaves no unresolved FAILED leaf does not dominate, while the
root's own severity and all escalations are preserved (see
`features-and-invariants.md` §Bounded failure-recovery). Task detail owns
subtask browsing. No New task flow is
exposed by this list.

### Current escalation reason display

Task detail (including its root recall-tree Outcome), `happyranch details`,
and the dashboard Waiting on you inbox use
the additive `escalation_reason` read projection. Ordinary escalations retain
their existing `task.note`, API values, and presentation. When the current
escalation audit is an `authority_v2_refusal`, `primary` comes only from an
`orchestration_step` whose decision is `escalate` within the current episode;
the previous and current `escalation` audit IDs are the exclusive episode
boundaries. The UI and CLI show that manager-authored text first, followed by
the server-owned plain-English explanation for the refusal code. A refusal
episode with no escalate decision shows only the explanation, and an unknown
code is shown verbatim. A resolved older episode is never searched. On task
detail the two labels (`tasks.detail.escalationReason`,
`tasks.detail.automaticEscalation`) follow the UI locale (W3b-1); the
`primary`/`secondary` values are daemon text rendered verbatim in every locale.

The read is exact-task scoped and bounded. It does not alter `tasks.note`, the
refusal finalizer, any audit payload, or the `audit_log.task_id` scope-prefix
contract. Task list rows and the resolution dialog do not display escalation
reason text; their behavior is unchanged.

### Thread-detail system rows

The live `ThreadDetailTranscript` renders system events through its local
`SystemDivider`: one separator above a monospaced description, inline icon
and task links, then trailing system-event metadata. Short and long rows use
the transcript content width inside the existing padding. Text and unbroken
identifiers wrap; the timestamp remains available in the row tooltip.
`describeSystem` displays the complete supplied task-completion summary and
escalation reason without a character limit, preserving line breaks and safely
rendering HTML-like content as literal text. It does not expose additional
payload fields. Ordinary message bubbles and terminal responder strips retain
their separate rendering paths. CLI transcript formatting is unchanged.

### Sidebar height and scrolling

The AppShell keeps the sidebar within the window height. Its organization
switcher and Settings/account footer do not shrink; the primary navigation
uses the remaining space and scrolls internally when needed. Navigation items
and typography retain their existing sizes, order and destinations. The rail
remains 244px wide on desktop and collapses to 56px below the `md` breakpoint.

The navigation container uses `min-h-0` to permit flex shrinking and `relative`
to contain the collapsed rail's absolutely positioned accessible labels.
Internal padding and scroll padding reserve room for keyboard focus rings.
Routed page content scrolls separately in its own container (for example,
`ContentWrap`), inside the shell's `overflow-hidden` main area.

`Sidebar.test.tsx` covers navigation/footer behavior and the overflow structure;
jsdom does not verify layout. For browser verification, use the supported
workflow in `web/scripts/screenshot-harness/README.md` with the real routed app
and valid local fixtures. Compare a short-window baseline with the correction;
check internal navigation scrolling, reachable footer controls, keyboard focus,
org switching, page scrolling, resizing, mobile collapse and both themes.
Record the source/build, viewport, screenshots, geometry and observed errors
in task evidence. Keep any font substitutions explicit when reporting visual
fidelity.

### Settings

The Settings surface ships as a full page (`web/src/features/settings/SettingsPage.tsx`) at the `/orgs/:slug/settings/*` route, entered from the footer-pinned **Settings** item in the Sidebar, with exactly four left sub-nav panels, in this order: Capacity · Assistant · Organization · Executors. The Settings root, retired `system` and `agents` subroutes, and unknown subroutes resolve to Assistant with replace navigation. (`SettingsDialog` is retained unmounted for direct tests; it is not an application or prototype entry point.) It shows:

- **Capacity** — stages the paired daemon-wide `queue_workers` and
  `host_global_session_cap` values for a future operator-controlled restart.
  Saving never applies live and the page cannot restart the daemon: the running
  readouts are observations and are unchanged by a save. The panel holds `base`
  (accepted only from a usable read or a usable success), the operator's
  `draft`, `latest` observations and an immutable `submission` record
  separately; a later read never silently rebases a dirty or unresolved draft,
  and only an explicit rebase / accept-latest moves `base`. Concurrency is the
  daemon's quoted strong `If-Match` revision. Numeric input is validated as
  canonical positive-decimal TEXT before any `Number` conversion, so an
  operator value outside the exactly-representable range is refused with the
  entered text preserved rather than silently rounded; that bound is an EDITOR
  representation limit and is not an API maximum. A server numeric that did not
  survive `JSON.parse` as a safe integer is withheld rather than displayed
  rounded — see the capacity note in `runtime-and-configuration.md` for the
  residual limitation this does NOT close. "Usable" means the same thing at
  every entry point: one capacity-local classifier decides it, and the provider
  applies it before a read observation is called usable or a write result
  enters the capacity cache, so a response the editor rejects can never become
  accepted cached data.
  Three refusals are load-bearing and are enforced at the request handler, not
  only on the control. A refresh that FAILED leaves the last usable values on
  screen under a `Last known` label with the receipt of the response that
  actually produced them — the retained values carry their own receipt even when
  a later request succeeds with an unusable body (that response still advances
  the separate provider receipt, but never relabels the retained values), and a
  byte-identical successful response still dates those retained values with its
  OWN receipt even though React Query structurally shares the value object —
  keeps the draft, reason and acknowledgment, and
  blocks the write until a genuinely successful, usable read recovers — a
  refetch result that merely carries cached data beside an error is not a
  success. An unresolved publication outcome (typed `config_publication_uncertain`,
  or a lost response / unclassified failure) is held as its own state: repeated
  refused Save clicks and an ordinary Discard change the banner and reset the
  editor but never resolve it, because neither establishes what the daemon
  persisted. And `latest` observations are ordered by the order this editor
  accepted them, so a newer verified read supersedes an older 409 body for both
  rebase and accept-latest; an accepted write then clears the reconciliation
  state its own response made obsolete and finishes in a coherent clean state.
  The SUBMITTING editor recognises its own write by the exact request: the
  capacity mutation slot (`CapacityMutationLike.settlementOf`; the shared
  `MutationLike` is not widened) reports which provider settlement that request
  object produced, recorded before the cache write, so the editor skips exactly
  that one observation whenever its render is flushed — never a window in
  which the request happened to be in flight and never the revision it
  accepted. A revision is a content hash, so another editor saving or restoring
  the same bytes is a different settlement and is observed normally. A
  receipt of the editor's own base revision is ordinarily a no-op (no notice,
  no target), but equality to the base is not proof that nothing happened
  since: when a pending reconciliation target names another revision, a later
  observation restoring the base bytes supersedes that target like any later
  observation, so rebase and accept-latest act on the restored revision
  rather than on an obsolete one, and the explicit choice is still required. Every
  observation that is not this editor's own settlement — including another
  editor's write that lands while this editor's request is still pending — is
  handled at once by the ordinary rules: adopted when clean, recorded for an
  explicit choice when dirty or unresolved. Nothing is held back awaiting the
  outcome, so a rejected, unknown or unusable own result leaves that external
  write available and still requires an explicit rebase / accept-latest before
  any further PUT. An accepted own write fences only observations that settled
  BEFORE its own settlement; one that settled after it is adopted by the
  now-clean editor instead of being cleared. Any other editor mounted on the
  SAME client therefore treats a write it did not settle as the external change
  it is, rather than staying stale on an older base and revision; its next
  deliberate save carries the adopted (or explicitly chosen) revision.
  Draft consequence arithmetic uses the RESOLVED per-key next-start values, so
  an environment-shadowed key contributes the environment's value rather than
  the draft the environment will shadow.
  Leaving the panel with an unsaved draft — or with an unresolved publication
  outcome — is intercepted by a confirmation dialog. **Stay on page** keeps the
  route, keeps the values, reason and acknowledgment byte-identical, and
  returns focus to the control that had it before the dialog opened;
  **Discard and continue** completes the navigation. The panel carries its own
  focus-ring and primary-action tone treatment, selected from existing tokens at
  the call site rather than by changing a shared primitive or token definition,
  because the shared ring token is translucent enough to fall below the accepted
  3:1 ring threshold on this screen's surfaces. That treatment covers the
  confirmation dialog too, including the dialog Close control the shared
  primitive renders: the dialog is PORTALLED, so it inherits nothing from the
  panel wrapper and is styled and measured explicitly.
  `scripts/screenshot-harness/capacity-states.mjs` gates both, at both desktop
  widths in both themes. It declares the finite set of keyboard OPERATIONS it
  requires — the details disclosure; the acknowledgment that enables Save;
  Check saved values after a real uncertain write, with its deliberate rebase
  and the separate manual save that follows; conflict rebase; conflict
  accept-latest and the separate manual save that follows it; the leave
  dialog's Stay and its confirmed departure — independently of what any run
  records, so a required operation that is missing fails the run exactly like
  one that failed. An unfiltered run is judged against that complete inventory
  at every viewport and theme whatever scenario list it executed, so deleting a
  scenario — its definition, its callback or its records — fails the run rather
  than deleting its expectation; only an explicitly filtered run narrows, and it
  is labelled partial and never reported as acceptance. Where
  an operation has a pure verdict (Check semantics, the C1 receipt sequence)
  the gate recomputes it from the recorded evidence rather than trusting a
  recorded pass, and a focused failed-reread control proves that cached values
  beside an error are not a successful Check. While the real portalled dialog
  is open — in its own isolated context, so a measurement can never confirm a
  departure — every dialog string is contrast-sampled and the enabled
  `Discard and continue` is measured through real rest / hover / active pointer
  phases; a missing sample, a missing phase or a failed one fails the run. It
  measures every focus ring after cumulative opacity, and self-tests its own
  computed-visibility predicate against positive and negative clipping
  controls. `--gate-selftest <MANIFEST.json>` runs that same acceptance gate
  over a recorded manifest and over finite corruptions of it — missing
  operations, a removed scenario callback, a deleted scenario passed to the
  gate exactly as the run would then pass it, a full-run caller narrowing
  viewports or themes, a failed operation, a weakened Check
  assertion, a missing walk, missing dialog samples, a C1 receipt that went
  backwards — and fails unless every one of them is fatal and the unchanged
  manifest passes. Because the shell scrolls internally, a state whose evidence
  is a top-of-panel banner — the retained-receipt / unverified-read /
  unrepresentable-value notices — declares that banner as its capture subject,
  and a subject that is not inside the captured frame fails the run. Two states
  drive the receipt rule end to end under a pinned browser clock: a usable
  read, a BYTE-IDENTICAL successful 200 (proved identical by the served body
  hash, not by unchanged values), then a failed read whose retained values must
  still carry the identical response's OWN receipt, and finally a usable
  recovery that advances it.
- **Assistant** — assistant status, setup/recovery, and assistant executor binding.
- **Org** (editable, Phase 2) — org-level settings: session timeout override, dreaming schedule (enabled, schedule time/timezone, catch-up-on-startup, agent mode, include/exclude agent names), browser-managed threads config (enabled and invocation timeout), and **working_hours** (THR-035: the Work-Hours Config UI — feature on/off switch, org-level eligibility selector, and the raw per-tier schedule blocks `default` / `teams` / `overrides`).
- **Executors** — effective machine executor registry, custom CLI lifecycle, and recovery.

The response includes operator-only `reviewer_agents` and `threads.default_turn_cap`; neither has a browser control. Removing the former System rail does not move queue-worker facts into Health. The retired maximum orchestration step setting is absent from the settings API and browser surfaces; the historical step counter remains monotonic telemetry, not an execution limit.

**Backend routes:**

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/orgs/{slug}/settings` | Read-only System + Org snapshot (includes the raw per-tier `working_hours` blocks for the reconciliation view) |
| `PUT` | `/api/v1/orgs/{slug}/settings/org` | Partial-update editable Org settings |
| `PUT` | `/api/v1/orgs/{slug}/settings/teams` | Worker-membership editing for teams |
| `GET` | `/api/v1/orgs/{slug}/work-hours/next-wakes` | Preview the next N wake timestamps for an agent's resolved effective schedule |

The serializer is an allow-list: no secret fields (permission_mode, codex_sandbox_mode, feishu credentials, daemon bind/port, allow_rules) are ever serialized. `extra='forbid'` on the PUT body rejects unknown/sensitive keys with 422. `save_org_config` deep-merges only allow-listed keys (`dreaming`, `threads`, `session_timeout_seconds`, `working_hours`) and carries through all unmanaged blocks verbatim. Tests recursively assert key-safety invariants (`tests/daemon/test_routes_settings.py`).

**Work-Hours Config (THR-035):** `working_hours` writes reuse the existing validate-then-atomic-write path in `save_org_config` — the candidate config is validated by `_build_org_config` / `_parse_working_hours` (the same path config-load uses), so an invalid config can never reach disk; the last-known-good keeps running. `enabled` is a single feature-level switch (never a per-tier or per-agent leaf); eligibility (`agents`) is a single org-level gate. A pre-flight validates working_hours agent/team names against the live roster (422 on unknown) before any write. Every working_hours write emits an audit row scoped to `config:working_hours` (who/when/before→after/tiers) via `AuditLogger.log_org_config_write` — reusing the established generic-scope-id convention on `audit_log.task_id` (no column change, no real-TASK-id overload). Validation is server-authoritative; the client does cheap format hints only. Routine-task editing is **read-only in MVP** (Phase 2 is the agent-contract-file write surface).

**Hot-reload:** Changes apply on next consumer read — dreaming scheduler picks up changes within ~1 min; threads/compose read on next request; session timeout applies to next session spawn. No daemon restart required.

The shipped frontend surface is `web/src/features/settings/SettingsPage.tsx` (left sub-nav + field panel per sub-route) with `lib/api/settings.ts`, `hooks/settings.ts`, and a `settings` domain in `DataContext`; the older `SettingsDialog.tsx` is unmounted retained test-only code.

### Agents page

The Agents page (`web/src/features/agents/`) shows the active agent roster plus pending enrollments. The mounted detail pane serves desktop and mobile; the retained drawer component shares the prompt editor without a new mount. Agent details include:

- **Repositories** — `repos` map from org/agents/<name>.md frontmatter (THR-095), shown as badge chips in the detail header.
- **System prompt** — collapsible, with Edit / Save / Cancel. The founder-only `PUT /agents/{agent_name}/system-prompt` accepts exactly `system_prompt` and the matching lowercase 64-hex `expected_revision` from `GET /agents`. Description remains read-only.
- **Model** — per-agent model string (`model` field in `GET /agents`, additive THR-067 field). Web UI field is PR-2 (separate follow-up).

Prompt saves preserve unrelated definition fields and normalize multiline/Unicode text through the existing parser and protected-input validators. The exact 200 receipt is `{agent, system_prompt, revision}` from one canonical byte snapshot. The editor freezes the draft base and captured org/target, disables typing/Save/Cancel through PUT and an explicit uncached GET, and shows Saved only when both body and revision match. Polling never rebases a dirty draft. Validated uncached prompt observations reconcile only the captured org/target prompt and revision in the roster cache, so leaving and returning preserves that observed base. Unrelated fields and agents stay unchanged; a newer prompt observed during readback wins and requires another explicit inspection instead of Saved. Ordinary reads already in flight are cancelled without reverting cached observations before reconciliation. Initial loading/error/empty/missing-revision/disappeared-target states refuse editing; close/selection/navigation discard locally and ignore late responses. en/zh-CN switching preserves the mounted draft, focus and selection. Errors retain the draft; inspect an explicit fresh read before deliberate reapply, with no automatic PUT retry. Cancelling the request can leave an admitted save committed: its started renderer and terminal reconciliation finish before the writer gate releases, so inspect fresh canonical state before reapplying.

Missing/malformed revisions return422 `expected_revision_required`; stale bases return409 `stale_agent_revision` with `current_revision`; absent active targets return404 `agent_not_found`. Reconciliation400 includes `system_prompt_reconciliation_failed`, a bounded diagnostic and actual canonical/workspace compensation statuses (`restored`, `not_owned`, `failed`, `not_required`). Audit500 reports `system_prompt_audit_failed` / `possibly_committed`, never an invented rollback. A canonical receipt does not prove workflow readiness or retained-provider adoption. Founder THR-280 seq21 waives C16 empirical genuine Claude/Codex/Pi/OpenCode adoption, including retained sessions and finite genuine Codex recovery episodes: FOUNDER-WAIVED / NOT EXECUTED, never PASS. Prompt delivery across launch/resume, all four adapters, full/delta/hole/fallback and finite callback-recovery orchestration remains required. Deterministic external CLI stubs can establish input transport/orchestration, not model adoption; THR-211 seq371 forbids real test-generated provider sessions in every venue.

Teams membership editing (add/remove workers only — manager reassignment is founder-gated) is available via `PUT /settings/teams`, wrapping `TeamsRegistry` mutators with `validate_team_membership` consistency checks and 409 rollback on drift.

**Backend:** The `GET /agents` response now includes `repos`, `system_prompt`, and `model` fields (additive, `allow_rules` remains excluded). The `PUT /agents/{agent}/model` route sets or clears the per-agent model (see below).

The agent detail pane also reads `GET /agents/{agent}/cleanup-activity`. It returns at most five newest distinct own-agent tasks eligible by a historical same-agent `workspace_cleanup_triggered` audit or the exact manual first line `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` (alone, LF or CRLF; no substring/prefix matching). Eligibility and deduplication precede the five-row limit. Routine reports stay on this existing agent page through ordinary completion/results, with no cleanup-report thread creation, routine posting or reuse obligation. Historical threads/messages/associations/results/audits and explicit founder-requested coordination remain intact. Manual display does not increment daemon count or grant action authority. A task's lifecycle status and latest same-agent result status remain separate; missing summaries are rendered as unavailable. This GET is a read-only projection and does not start or perform cleanup.

Build and dev commands:

```bash
scripts/build_web.sh
cd web && npm run dev
happyranch web
```

The SPA fetches the daemon bearer token once via `GET /api/v1/auth/bootstrap`, which is localhost-gated, then caches it in `sessionStorage` and attaches it to HTTP and SSE calls. CLI bearer-token behavior is unchanged.

## CLI

The CLI is an HTTP client. Start the daemon first.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
happyranch web [--no-open]
```

### Team-manager agent updates

`happyranch manage-agent update` is a task-session callback, not a browser
Agents-page feature. Read the active `GET /agents` roster first, compose the
intended whole-definition update from that exact row, and pass its `revision`
as `expected_revision` (or `--expected-revision` for the direct CLI form).
The daemon returns 422 for missing, null, or malformed revisions and 409 for a
stale base. On 409, reread and deliberately reapply the intended field change;
do not pair older composed content with a newer roster revision.

### Workflow template authoring (U1B)

U1B publishes inert immutable `product-design` template definitions; it does
not activate or execute them. An active, uniquely registered team manager uses
its verified task/session binding, and the server derives its organization,
principal and `org/<org>/team/<manager-team>` namespace:

```bash
happyranch workflows templates publish --org <org> --from-file /absolute/template.json --session-id <session-id>
```

Founder omits `--session-id`, supplies `team_slug` in the JSON payload, and the
command uses the existing daemon bearer. The payload contains
`operation_key`, `template_name`, `expected_current_version`, `definition`,
and (Founder only) `team_slug`; publisher, principal, namespace, org, task and
session claims are rejected. `--from-file` must be absolute. The closed
definition is `kind=product-design`, with `schema_version` set to the genuine
JSON integer `1` (not a boolean, float, string or null), a Product Lead agent
author of an immutable PRD revision, Founder/implementer/tester reviewers, all
three required on the current revision, and request-changes returning to the
author. A non-empty description is the only variable descriptive field;
unknown fields and kinds fail closed.

Founder reads exact immutable versions with:

```bash
happyranch workflows templates list --org <org> --team <team> [--json]
happyranch workflows templates show --org <org> <team> <template-name> <version> [--json]
```

The matching APIs are `POST /api/v1/orgs/{slug}/workflows/templates/publish`,
`GET /api/v1/orgs/{slug}/workflows/templates?team_slug=...`, and
`GET /api/v1/orgs/{slug}/workflows/templates/{team_slug}/{template_name}/{version}`.
Responses return canonical JSON, base64 of the same UTF-8 bytes, SHA-256,
version/pins/timestamp and authenticated publisher provenance. Conflict,
stale-CAS, duplicate-content and authorization errors retain stable
machine-readable `detail.code` values; refusals leave no template residue.

### Workflow cutover

Founder bearer and actual org authority apply to all three methods:

```bash
happyranch workflows cutover show --org <org> [--json]
happyranch workflows cutover request --org <org> --from-file /absolute/request.json [--json]
happyranch workflows cutover downgrade-preflight --org <org> [--json]
```

The only request fields are `action` (`enable` or `disable`), an opaque ASCII
`operation_key` (1–128 characters from `[A-Za-z0-9._:-]`), and a strict positive
integer `expected_generation`. No client actor/org/owner/proof/verified/state or
receipt claim is accepted. Disable reason is fixed `founder_disable_requested`.
The API prefix is `/api/v1/orgs/{slug}`: GET `/workflows/cutover`, POST
`/workflows/cutover/requests`, GET `/workflows/cutover/downgrade-preflight`.
`workflowCutover.ts` mirrors these methods; there is no workflow UI page yet.

Responses expose stored marker/events and derived allowed actions, blockers,
actual owners, deferred actions, reconciliation_required and the reconstructed
`workflow-cutover-verifier@1` verification event. Requests add original request
identity/generation/action and replayed alongside current state. HTTP200 means
accepted, including truthful pending verification/drain. Retry an ambiguous
response with the same body/key; exact historical replay works after disable
and cold reopen. It never creates a new request identity.

Auth/org authority precedes body parsing; identity claims return403, strict
invalid input422, corrupt layout/history500, operation conflicts/stale CAS/
illegal edges409, precommit operational failure safe500. Errors expose only a
machine category. CLI exits0 for accepted reads/requests (including pending) or
eligible preflight, 1 for denied preflight/domain/input/transport failures, and
2 for usage. An existing F enable refuses before event/CAS writes with the
existing500 category `draft_schema_migration_required`; GET's existing blocker
`required_action` names `python scripts/migrate_workflow_draft_schema.py
--runtime-root <absolute-root> --org <slug>`. The operator runs that script
explicitly; `--check` exits3 when needed, 0 ready, 1 refusal, 2 parser. Existing F
history remains visible without recovery advancement. New `orgs init`/POST /orgs
creation initializes complete E directly. Preflight is read-only and permits only
pristine F initial history with no workflow data; every E needs a compatible
reader, including empty new/migrated orgs; even empty drained or template-only stores refuse.
The pinned preceding reader accepts pristine F and refuses E; older binaries
are not claimed to enforce the current cutover decision. Activation, first-draft
work and cancellation/dispatch remain later units; no live enable or deployment
is implied by these methods.

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands take no `--org`.

System assistant commands are container-level:

```bash
happyranch assistant init [--repair|--reconfigure]
happyranch assistant status
happyranch assistant
```

`happyranch assistant` shows the system assistant configuration status;
`happyranch assistant init` and `happyranch assistant register` manage the
assistant. It does not take `--org`.

### Task work-status summary (TASK-5522)

`happyranch details <task_id>` prints a compact **Work status** block derived
server-side from the task record + audit rows (`runtime/daemon/work_status.py`)
and served on the existing task-detail envelope — no extra endpoint:

```
Work status: Stale-but-alive — no substantive update recorded
  Start:      2026-08-23 21:06:02
  Heartbeat:  2026-08-23 21:39:30 (fresh)
  Update:     No substantive update recorded
```

- The **Start** line is the current-session `session_start` audit timestamp
  (latest assigned-agent session; a prior session's receipts never count).
- The **Heartbeat** line is the last heartbeat with an explicit freshness
  suffix using the existing 60-second semantics (`fresh`/`stale`/
  `unavailable`) — it never claims execution progress.
- The **Update** line comes ONLY from a real `progress` audit receipt
  (timestamp + concise agent-written milestone). When none is in scope it
  prints the explicit `No substantive update recorded`; a live session whose
  start or last receipt is ≥ 5 minutes old renders `Stale-but-alive …`
  (policy `STALE_PROGRESS_AFTER_SECONDS = 300`).
- Terminal / pending / escalated / parked-on-block tasks print `Not
  applicable` with a reason — never an implied live agent.

The full audit log (including inline `progress` messages) is unchanged.

The founder-facing web surface is the **A-mode Cmd-K dock** (structured chat
docked in the AppShell, toggled via the AppBar / Cmd-K shortcut). Assistant
configuration (status / init / register / repair) is served over four HTTP routes
(in `INCLUDED_PATHS` with TS mirrors in `web/src/lib/api/assistant.ts`).
There is no standalone `/assistant` web page, no xterm terminal, and no
"Open full session" escape hatch — the dock is the sole assistant surface.

### Org portability (Slice A)

CLI-only, relocation-only safety surfaces (no UI / TS client / browser
contract). Slice A is preflight + reconciliation only — it creates no archive
and performs no export/import.

```bash
# Read-only: classify every direct org-root child + report quiescence blockers
happyranch orgs portability-preflight <slug>

# Founder/master-bearer-only: reconcile exactly one confirmed zombie
happyranch orgs reconcile-portability <slug> --from-file /tmp/reconcile.json
```

`reconcile-portability` request JSON names one candidate plus evidence and a
disposition (`cancel` or `consume_result`):

```json
{"candidate_task_id": "TASK-123", "disposition": "cancel", "evidence": {"reason": "dead pid + stale heartbeat"}}
```

The `--from-file` path must be absolute. See `docs/agent-guides/features-and-invariants.md`
(Org Portability) and `docs/agent-guides/orchestrator-contracts.md` (Organization portability)
for the exhaustive root allow-list (including `work_hours`), quiescence/zombie
reporting, the conservative schedule policy (any armed or firing schedule
refuses, with existing-control remedies only), and reconciliation limits.

Full founder-facing CLI docs: `skills/happyranch/SKILL.md`.

### Memory report guard

The existing `/audit` read surface returns render-observed impression JSON with
`memory_telemetry_version=1`, `pointer_ids` and `full_body_ids` alongside the old
agent/session/digest/count/budget fields and unchanged actual task row scope.
Fit directives are full-body exposure; fallback directives are pointers; IDs
mentioned only in item text are excluded. No content is logged. Old writer
calls remain byte-equivalent and unversioned. Read/search routes and CLI hint
precedence stay unchanged; digest-first source resolution uses accurate item
IDs, allowing a genuinely nonshown search-result read to be search-sourced.
Only appended items with string IDs satisfying the existing `ID_RE.fullmatch` contribute identity metadata. Null, missing, nonstring or malformed IDs remain rendered byte-for-byte under the existing permissive parser, but contribute no identity or ID fragments from their representation, title or body. A malformed-only digest still launches normally and emits no impression; valid neighbors retain exact modes/counts. Strict writer validation still rejects malformed caller-supplied metadata before insertion.
A version is exposure metadata, never epoch authority or task eligibility.
The source-side G3 census records actual bootstrap intent/identity/expectation/
binding/launch-callback/terminal metadata plus independent seals in ordinary audit
rows. Existing `/audit` preserves entries/cursors and optionally adds
`memory_collection_observation` only for `action=memory_collection_seal`, from
the actual serving OrgState. Other action responses remain unchanged. The
closed source view and loaded identity are defined in the corrective memory
spec; absent/busy/moving/failed components are explicit unknown/unavailable.
GET performs no durable writes, resealing, provider launch or backend probe.
Both epoch refs remain null; the CLI gains no collection authority.
Stored seals checkpoint counters only: `census_integrity` explicitly reports
`census_not_reconciled` with `census_valid=false`. Exhaustive integrity is a
bounded internal zero-write validation with live semantic bookends; neither a
stored seal nor a valid source census establishes collection health.
G1 independent canary/epoch acceptance and current-serving census acceptance
remain OPEN.
Read-side G4 acquisition is implemented below; full eligibility reporting and installed health remain OPEN.

`happyranch memory report` paginates the existing audit read surface but is
currently fail-closed: JSON and text both return `insufficient_instrumentation`.
There is no CLI flag or input that can override the invalid current/unversioned
epoch. Executor-owned task children receive their runtime session in the private
`HAPPYRANCH_RUNTIME_SESSION_ID` environment hint, so `memory get`/`search`
without a flag can preserve validated read-only attribution; a
nonempty `--session-id` still takes precedence; an explicitly empty flag retains
the existing environment fallback. Environment assembly with no runtime SID
sends an empty hint that overrides inherited ambient values. Ordinary/custom
execution forwards resolved invocation IDs; generated, unregistered IDs remain
uncredited. Provider resume IDs are never used for this purpose.
The report neither begins collection nor recommends push, alias, embedding, or
ranking changes.
The backend and canonical CLI share the pure `memory_telemetry_report` reducer.
They acquire `session_start`, impressions, reads and searches before any empty or
short return. Backend acquisition uses one synchronized SELECT statement snapshot;
CLI exhausts real `/audit` pages at limit5000 in two complete sweeps and compares
relevant audit identities/content and `/agents` roles at one aware UTC cutoff.
Events at or after that cutoff are excluded. Backdated/content/role drift,
cursor/schema errors, HTTP/decoder/timeout or SELECT failures refuse with
`acquisition_unavailable`; CLI writes only that category to stderr and exits1,
with no partial JSON. Unavailable `/agents` stays explicitly unknown and allows
safe descriptive counts; it never supplies functional cohort authority.

Structural returned-data corruption produces the full empty-metrics error object
(null first event, days0, empty aggregate/by_agent/by_role/read_counts, explicit
errors). Task-only sources are `digest`, `search`, `explicit_or_other`; unsupported
strings use `invalid_source`, nonstrings `malformed_read_source_type`, and causal
or digest-first contradictions `source_contradiction`. Independently identified
manual/thread/dream/recovery/legacy populations are excluded before task source
validation. Runtime task credit requires the actual task/agent/runtime-session
start tuple; prefixes, client claims and SID-only matching cannot establish it.

Version1 observed pointer/full-body lists are validated, disjoint and duplicate
free, with union matching digest IDs/count. Legacy impressions remain unversioned
with unavailable mode metrics; no memory-file/body/directive inference occurs.
Exact tuple+memory pairs deduplicate opportunities and reads; secondary activated
sessions and per-agent/role/memory operation counts remain descriptive. Search
ratios use distinct persisted, causally corroborated search-sourced read pairs.
Earliest qualifying impression is a deterministic aware-UTC minimum. Elapsed
complete UTC days exclude partial first/current days. Raw day/session sample
flags may be true; thresholds, diagnostics_valid_for_collection and
evaluation_candidate remain false, decision `insufficient_instrumentation`.
`session_start` records audited intended invocations, never a complete process
launch/expectation census. G3 source observation is shipped separately; the report does not acquire its
current-serving census authority. Trusted canary/epoch, census and probe
health remain UNKNOWN/unavailable in the report. There is no collection/tuning, ranking write,
synthetic/natural eligibility claim, authority override or epoch start.
The read-side snapshot/two-sweep contract detects observed drift; it adds no
writer fence or hostile same-UID guarantee. Full eligibility, operational H-v1
and actual shipping/installed canary cases remain separately gated.

### PR CI wait / guarded merge entrypoints

Two CLI entrypoints (invoked as jobs or on task resume, not as `happyranch` subcommands) provide the PR CI polling and guarded-merge mechanisms:

```bash
# Poll job (submitted via happyranch jobs submit):
python -m runtime.daemon.pr_ci_waiter \
  --repo owner/repo --pr N --head-sha <40-char-sha> \
  --expected-check "Python CI" --expected-check "Web CI" \
  --timeout-seconds 3600 --settle-seconds 120 --poll-interval-seconds 15

# Merge (triggered by resumed task):
python -m runtime.daemon.pr_ci_merge \
  --org <org-slug> --repo owner/repo --pr N --head-sha <40-char-sha> \
  --merge-method squash --ci-verdict ci_pass \
  --review-task-id TASK-xxx --qa-task-id TASK-yyy
```

Both print structured JSON verdicts to stdout and exit with mapped codes (0 = success).
The review/QA evidence extraction follows the **Merge-evidence contract** in
`docs/agent-guides/orchestrator-contracts.md`: the canonical vocabulary
`APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL`, NON-NULL structured
`verdict` primary (canonical token only), serialized `null` (the durable
recall producer's representation of legacy/no-structured rows) using the
strict annotated prose (`Verdict: PASS — rationale`) fallback, and fail-closed
rejection of missing/contradictory/malformed/ambiguous evidence and unusable
non-null structured values.
The poll job runs with `review_required=false` through the existing jobs path; agents never
get raw `gh pr merge` grants. The full workflow narrative (submit → blocked → resume → inspect →
merge/revise) is documented in `runtime/skills/bundled/jobs/SKILL.md` and
`docs/agent-guides/features-and-invariants.md`.

### Per-agent model selection

Set or clear the model an agent uses via the `set-model` CLI command (mirrors `set-executor`):

```bash
# Set a model
happyranch set-model --org <org> dev_agent --model claude-sonnet-5

# Clear — revert to CLI default:
happyranch set-model --org <org> dev_agent
```

The backend route is `PUT /api/v1/orgs/{slug}/agents/{agent_name}/model` with payload
`{"model": "<id>" | null}`. It reconciles the org `.md` frontmatter (`model:` field) and
the org/agents/<name>.md frontmatter in one call (THR-095).

When a model is set AND the executor profile has a `model_arg` template (all four built-in
profiles do — verified per CLI), the executor injects the substituted model flags into the
CLI argv at launch time. When unset, each CLI uses its own default model (today's behavior
for every existing agent).

Executor changes and model overrides are coupled: changing an existing
agent's executor clears its old configured model, while selecting the already
configured executor preserves it. In the team-manager `manage-agent` update
contract, an explicitly supplied `model` (including `null`) is the model choice
for the new executor; if `model` is omitted during a real executor change, the
old override is cleared. The Agents web editor saves a dirty executor before a
separately dirty model and refetches the agent list after each mutation, so an
explicitly edited model is applied as the new choice and an untouched old model
is not restored from stale client state.

### Executor binary registration

Register the absolute path to each executor CLI binary so the daemon can locate it at
spawn time (THR-085). The daemon resolves binaries exclusively from the machine-local
``executors.json`` registry at launch — there is no PATH fallback (THR-107 seq155).
Registration is the sole availability gate; headless daemons and fresh machines must
have every executor explicitly registered.

**Exception — custom-adapter profiles:** profiles with
``command_adapter_id: custom-adapter:<id>`` use the exact founder-APPROVED,
hash-verified absolute adapter executable as their launch artifact — they do **not**
require a separate ``executors.json`` record keyed by their profile name.
``executors.json``.

```bash
# Register with explicit path (required):
happyranch executor-binaries register claude --path /opt/homebrew/bin/claude

# List all registered binaries:
happyranch executor-binaries list

# Conditionally remove a binary registration (kind + exact path must match):
happyranch executor-binaries remove <kind> --expected-path <absolute-path>
```

`--path` is **required** — omission does NOT fall back to PATH resolution
(THR-107 seq155). The operator must supply an explicit absolute path.

``happyranch executor-binaries remove`` atomically deletes a binary registration
when both ``kind`` and ``--expected-path`` match the stored record exactly:
200 prints the removed registration; 404 reports no registration; 409 reports a
stale observed path (refresh with ``list`` and retry); 422 reports validation/
built-in-protection/name-mismatch errors. This command removes machine-local
binary registrations only — it does **not** delete adapters, profiles, or other
daemon state.

**Custom-adapter lifecycle management (THR-107 slices 1–3).** The ordinary
Settings/onboarding "Connect a CLI → connect a custom CLI instead" flow is
now instant: the founder mints a token by naming the CLI — nothing else —
the candidate CLI's copy-pasted script both writes its wrapper at the
daemon-issued path and POSTs it directly, and the browser auto-finishes the
connection the moment it lands — no PENDING wait, no founder-approval
click, no separate Bind step. The wrapper's own `/connect` POST declares
its `workspace_adapter_id` (which workspace-bootstrap convention its
agents should use); the founder never picks this — only the wrapper author
knows which convention their CLI expects. Approved-unbound recovery
affordances, the pending-approval queue, and the standalone Bind card were
**removed from the ordinary UI** in slice 3 (not hidden behind an advanced
panel) — a connect that doesn't finish shows a retryable "Connection
failed" card instead.

Adapter-backed custom CLIs are surfaced inside **Settings → Executors →
Custom CLIs**: their approved executable is joined to the profile row by the
``command_adapter_id: custom-adapter:<id>`` reference. The authenticated
``DELETE /api/v1/runtime/adapters/{adapter_id}`` route remains available for
API-level cleanup — removal only deletes the durable registration entry
(never the on-disk executable) and writes an ``adapter_removed`` audit row
(scope ``adapter:<id>``).

The legacy ``POST /runtime/adapters/{id}/approve|reject|bind-profile`` routes
(and their ``happyranch``-adjacent TS bindings in ``web/src/lib/api/
adapters.ts``) are preserved, unchanged, as **operator-only one-time
disposition tooling** — no normal-flow UI calls them anymore
(``tests/contract/route-classification.json`` reclassifies them as excluded,
no browser consumer). Reach for them only for manual/scripted recovery of a
legacy PENDING/approved-unbound record; a new custom CLI should always use
the ordinary Connect flow instead.

### Token usage

`happyranch tokens` shows `session_token_usage`. Default is the most recent rows;
a `--by-*` flag (mutually exclusive) switches to a rollup:

```bash
happyranch tokens --by-agent | --by-task | --by-thread | --by-purpose
```

`--by-purpose` groups by `invocation_purpose` (route `group_by=purpose`). Filters
(`--since`, `--thread-id`, `--agent`, `--purpose`, `--scope-type`,
`--scope-id`, `--task-id`) AND-compose with any view.

Usage v1 efficiency cohorts do not derive historical executor/model from this
usage view or from current agent configuration. Lifecycle records capture the
effective launch tuple: thread invocations use nullable executor/model columns,
task `session_start` adds runtime session ID + actual spawn purpose +
executor/model, and `dream_started` adds executor/model. NULL remains unknown;
there is no historical inference or `"default"` sentinel.

The read-only Usage v1 API is `GET /usage/workload?compare=bool` and
`GET /usage/efficiency?compare=bool`. Efficiency without a cohort returns
options and unattributed counts; a selected cohort requires `executor` plus
exactly one of `model` or `model_unpinned=true`. Both routes use the same
bearer authentication and org scoping as `GET /tokens`. They return rolling
seven-day UTC bounds with the resolved org timezone for display. Workload
emits only current-window agents. Each period's
`reply_outcome_coverage` reports linked `recorded` replies out of
`total_consumed` REPLY wakes; a NULL `reply_message_seq` is unknown, does not
count as a Reply, and withholds only the Replies delta when comparison is on.


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
also translated. Preview stays unset-English with
Chinese opt-in, and W5/native N0/N1 remain deferred. Browser evidence:
`web/scripts/w4a-browser-evidence.mjs --slice usage` (ordinary bundle).

The web Usage page (`/orgs/<slug>/usage`, `web/src/features/usage/`) is the
Usage v1 UI over these two routes and no longer reads `GET /tokens`. It shows
a fixed "Last 7 days" window taken from the response (with Data through and
generated-at in the response timezone) and a Compare toggle that is off by
default. Workload lists exactly the returned agents: Task runs, Thread wakes,
Recorded runtime (agent runtime with known/total coverage; missing runtime is
"Not recorded", never zero), Deliveries (unclassified results are footnoted),
and Replies (recorded X of Y, with the unknown portion labelled "reply outcome
not recorded"). Efficiency renders nothing until exactly one CLI and then one
model are chosen from `cohorts`; the NULL-model cohort is its own "CLI default
(not pinned)" option, and there is no aggregate option. The five run-type rows
always render in fixed order. A null class reads "Not reported", or "Unknown"
when no run in the row has usage, and is never zero. A partial Fresh input is
labelled with its class denominator, and decline waste appears only on the two
thread rows. Unattributed lifecycle counts are listed outside the rows. Every
delta is rendered from the server's `UsageDelta`, with neutral styling. A
withheld delta shows a dash, both periods' coverage and a plain-language
reason, never the raw code. Loading, per-view error with retry, stale (earlier
figures kept after a failed refetch), and empty states are explicit. Below
`md` both tables scroll sideways under a frozen identity column. There is no
export, cost, or blended token total.

Rollup modifiers (presentation-side; require a `--by-*` flag):

- `--top N` — rank by churn (`total`) DESC and keep the top N; ties: sessions DESC then key ASC.
- `--over-threshold N` — keep only groups whose churn strictly exceeds N (applied **before** `--top`); empty result prints a "nothing would alert" line.

**Churn invariant:** `total = input + output + reasoning`. `CacheR`
(cache reads) rides in its own column and is **never** folded into `total`
or used to sort/threshold — it overstates burn ~10–100×.

The `--by-agent`/`--by-thread` rollups add a **Model** column
(none on `--by-task`/`--by-purpose`). Its label is classified at render time —
a single presentation constant `MODEL_FIX_CUTOVER_TS` draws the pre/post line,
never SQL:

| Label | Meaning |
| --- | --- |
| `<model-id>` | one observed model |
| `(mixed)` | >1 model, or observed+NULL mixed, or NULL spanning codex+claude |
| `(cli-unreported)` | all-NULL codex (codex emits no model field) |
| `(unknown — pre-fix)` | all-NULL claude, all before the cutover (frozen history) |
| `(unknown — ANOMALY)` | all-NULL claude, any at/after the cutover (parser-drift canary) |

The founder dashboard carries a read-only **Top token threads** card (a
window selector for 24h/7d/30d) backed by the same `/tokens?group_by=thread`
route. It ranks threads by churn (`total`) DESC client-side, shows cache reads
as a muted secondary number (never in the bar or the rank), and labels each
thread's Model with the same precedence as the CLI table above.

### Staged dual-text escalation-policy editor (THR-229, draft PR878)

This surface is present only on the unmerged draft feature branch; it is not a
running-production capability and performs no live activation. Every roster-
confirmed manager with exactly one matching team registration has a dedicated
policy route showing exactly the editable
`What to escalate` and `What not to escalate` textareas. A genuinely empty
selector begins with the server-projected neutral starter bytes and uniform
per-team derived ID/title; there is no client starter constant or Engineering
special case. An active v2 selector reads
both values and its release/version/digest, activation, selector and epoch from
the authenticated projection. One confirmation sends both texts through the
existing paired v2 release transaction. It never exposes the legacy split
create/activate sequence, a one-text save, clause IDs, continuation phrases,
rollback controls or a second evaluator.

The client keeps a dirty pair across ordinary query renders and guards both
SPA navigation and hard unload. It treats pending submission as single-flight,
accepts success only after the paired receipt and authoritative refetch agree,
reuses the exact request after an ambiguous outcome, and preserves the draft
on selector conflicts until the operator deliberately reloads the current
selector. Policy queries wait for both roster and team data, are keyed by the
exact organization/manager/team tuple, and are evicted immediately on tuple loss
or change to prevent cross-team flashes. The v2 history stream is immutable and read-only, shows both exact
texts plus full release/activation/selector identity and timestamps, and owns
pagination/error retry independently. The legacy history and outcome APIs/data
remain available, but the two legacy read-only UI sections and their eager
requests are removed. Unsupported, mixed, corrupt,
unknown and worker targets fail closed without an editable fallback.

For a v2-bound manager escalation, the injected role guidance supplies the
binding identities and requires one structured `manager_self_evaluation` beside
the ordinary `decision`. The active policy block is authoritative for its exact
object shape and supplies a filled launch-bound example in that manager-decision
task; managers set both assessments honestly rather than treating the displayed
values as defaults. Policy blocks rendered for thread, wake, schedule, and dream
contexts have no bound manager-decision identity and show shape/type placeholders
only, not values to submit. A manager self-evaluation is submitted only from the
manager-decision task's report-completion using that task's bound root/session
identities.
`happyranch report-completion --from-file` preserves that member for strict
server validation; omission, explicit null, malformed or uncertain evidence
fails closed. The CLI does not embed policy prose, a clause identifier, a
canonical continuation phrase, or a second evaluator. Worker and ordinary
non-escalation callbacks omit this field unless their injected contract explicitly
requires it.

The checked-in browser receipt driver is
`web/scripts/screenshot-harness/shot-thr229-v2-policy.mjs`. It uses an owned
runtime/database, loopback daemon and Vite proxy with real bootstrap auth; it
proves empty bootstrap, paired persistence and full-reload readback/history, a
two-text atomic rollback at the control-audit boundary followed by exact retry,
invalid-token 401 with no row, and worker 404 with no editor. This checkpoint
does not close the combined THR-229 feature, final parity, review/QA, merge or
rollout gates. Source merge is not activation: compatible daemon, CLI, and
manager-launch code must be deployed and old launch/completion consumers drained
before any future production save/activation. Rollback is compatible-code-only;
this candidate performs no deployment, save, activation, or natural production
continuation.

## Agent-Side Callbacks

These are invoked by skills inside agent sessions. Do not invoke them by hand; doing so falsifies audit data.

- `happyranch report-completion`
- `happyranch progress`
- `happyranch memory {add,update,promote,reindex}`
- `happyranch manage-agent`
- `happyranch manage-repo`
- `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

Callbacks should use `--from-file <path>` where payloads have multiple fields. **The path MUST be absolute** (e.g. `/tmp/completion.json`). A relative path silently resolves against the agent's cwd and can litter stray files under the runtime orgs root. The CLI rejects relative paths with a clear error in the callback family (`report-completion`, `threads reply/decline/dispatch/compose`). See `docs/agent-guides/agent-executors-and-permissions.md`.

### System Assistant mounted copy (THR-118)

The global `AssistantDockHost` and its mounted `ConversationSwitcher` bind en/zh-CN app-owned visible/accessibility copy, including composer/state/key hints, typing/tool activity and conversation actions/rename/delete confirmation. Shared MessageBubble/TypingBubble copy overrides are reused. Errors retain capture-time provenance: app fallback keys resolve at render time; raw daemon detail/message and caught diagnostic values (including empty or catalog-equal values) stay exact. Executor/tool names, titles, authored content and IDs remain data. Viewer-local timestamp and elapsed semantics remain unchanged.

Locale switches preserve mounted nodes, active conversation, transcript/inflight state, drafts, focus and selection without entering connection-effect dependencies or issuing requests/mutations/reconnects. The detailed boundary is [Assistant Web UI §6.12](../superpowers/specs/2026-06-12-system-assistant-web-ui-design.md#612-mounted-dock-locale-presentation-thr-118). Ordinary-build evidence is `web/scripts/w4a-browser-evidence.mjs --slice assistant`: real HTTP/WS seams and request/socket ledger plus 390/1440 screenshots. Coverage inventory includes both consumers and marks only `system-assistant` translated in this slice, supported by component regressions and ordinary-build mounted-copy evidence. Preview stays unset-English/Chinese-opt-in; W5 and native restart acceptance remain separate.
