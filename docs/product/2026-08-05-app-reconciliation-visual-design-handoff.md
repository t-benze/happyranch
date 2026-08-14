# App-reconciliation visual design handoff

| Field | Value |
| --- | --- |
| Status | design handoff; no implementation authorization |
| Date | 2026-08-05 |
| Owner | Engineering review of [#572](https://github.com/t-benze/happyranch/issues/572)–[#577](https://github.com/t-benze/happyranch/issues/577) |
| Baseline | [PR #555](https://github.com/t-benze/happyranch/pull/555), its [system-surface handoff](https://github.com/t-benze/happyranch/blob/task/TASK-4198/docs/product/2026-08-05-system-surface-visual-design-handoff.md), and THR-140 TASK-4373/TASK-4377 reports |
| Commitment boundary | This specifies visual/interaction targets only. It does not change application code, routes, APIs, browser authority, Work Hours semantics, or scheduler behavior. |

## Review disposition

| Issue | Verdict | Reason |
| --- | --- | --- |
| [#572](https://github.com/t-benze/happyranch/issues/572) | Approve as written | The Dreams list and overview currently expose invalid numeric values as factual copy. A truthful unavailable treatment is a bounded UI correction. |
| [#573](https://github.com/t-benze/happyranch/issues/573) | Revise | The TODAY sentence is currently derived from `narrative_counts.escalated_open`, while the card renders the routable `escalations` list. Without an approved API change, both signals must derive from that same list; do not invent a missing row. |
| [#574](https://github.com/t-benze/happyranch/issues/574) | Revise | The API supplies `name`, `size_bytes`, and `modified_at`, not authoritative source provenance. The design may show the supplied modified time and filename-derived provenance when present, but must label both truthfully. |
| [#575](https://github.com/t-benze/happyranch/issues/575) | Approve as written | `POST /assistant/register` validates, bootstraps, saves, and returns the current state in the same request. The UI should explicitly say registration is live-applied; it must not imply a daemon restart. |
| [#576](https://github.com/t-benze/happyranch/issues/576) | Founder-approved in THR-140 | Settings → Organization is the sole editable owner for Work Hours enablement and eligibility. It preserves the existing organization-settings write boundary and scheduler contract; implementation remains a separate PR and review. |
| [#577](https://github.com/t-benze/happyranch/issues/577) | Founder-approved in THR-140, after #576 | A navigation-only AppShell grouping pilot. It preserves routes, deep links, and compatibility redirects; implementation remains a separate PR and review. |

## Shared design rules

These rules apply to every frame below.

- Desktop frames are 1440×900 in light and dark themes. The current shell, organization context, route path, and existing design-system tokens remain the starting point.
- Use existing primitives and semantic tokens: `ContentWrap`, `PageHeader`, `Button`, `EmptyState`, `IdBadge`, `StatusBadge`, `Tabs`/`SubTabBar`, `Dialog`, `Tooltip`, `Sidebar`, and `AppBar`; use semantic surface, text, border, attention, danger, positive, spacing, radius, type, and shadow tokens. No raw colors, one-off shell, or design-system rebuild.
- Every value is either sourced, explicitly derived, or explicitly unavailable. A visual label never establishes an authorization rule; server responses, including 401/403, remain authoritative.
- Keep native links/buttons semantic. Provide visible keyboard focus, text plus non-color state cues, accessible names for icon controls, landmarks, and focus return from dialogs/drawers.
- At narrow widths, preserve readable content, reachable controls, and un-clipped focus. A responsive-shell redesign is not in scope; document any remaining limitation instead of treating it as solved.

## #572 — Dreams unavailable/invalid counts

**Target route and states:** `/orgs/:slug/dreams`; list-card count strip and Overview rail in populated, partially unavailable, loading, empty, error, and stale/disconnected states. The detail drawer follows the same rule when it presents count fields.

**Layout and interaction:** retain the current feed plus right rail. Keep local date and valid counts in the existing mono/tabular count strip. If either count is missing, non-numeric, or non-finite, replace only that value with an explicit `Unavailable` label (or `— unavailable` where space is constrained); do not coerce an unknown value to `0`. Use `0` only for a valid supplied zero. The rail must not sum invalid values into a total. No new click target, metric, provenance claim, or candidate action is introduced.

**Component/token mapping:** existing Dream card, `CrescentMoonBadge`, right-rail section, `EmptyState`, semantic `text-text-muted`/`text-text-secondary`, and mono numeric style. Unavailable is text, not a neutral-looking number or a color-only state.

**Accessibility and responsive:** announce the unavailable qualifier as part of the count text; preserve card button names and drawer focus behavior. On a narrow layout, count text may wrap but cannot be truncated into an ambiguous bare dash.

**Preserve / do not design:** Dreams records remain the source; do not add a new aggregate endpoint, fabricate candidate provenance, or change accept/dismiss authority.

**Acceptance evidence:** light/dark desktop frames for valid non-zero, valid zero, missing, and malformed count fixtures; a deterministic unit/browser assertion that `undefined` and `NaN` never render; keyboard-opened detail drawer and error/retry frame.

## #573 — Dashboard attention signal

**Target route and states:** `/orgs/:slug/dashboard`, specifically the TODAY narrative and `Waiting on you` card, for zero, one, and many routable escalation rows plus an intentionally mismatched summary/list fixture.

**Layout and interaction:** `Waiting on you` remains the primary, serif-title attention queue. The TODAY copy may say an item needs attention only when the same rendered escalation list contains a routable row. Each claimed item must retain its existing canonical task link/action surface; the dashboard is triage, not a new resolution surface. If the list is empty, use the existing calm `All clear` treatment and matching TODAY copy. If a payload counter disagrees with the list, prefer the list for browser-visible action copy and do not display an unexplained count.

**Component/token mapping:** retain `DashboardLayout`, current `Panel`, `EmptyState`, `EscalationInboxRow`, `IdBadge`, and semantic attention/status tokens. Do not turn the overview stat into a decorative warning independent of a canonical record.

**Accessibility and responsive:** the queue heading and each task link must form a navigable, keyboard-visible path; state text cannot depend on the attention color. The card must retain enough row context at narrow widths for the canonical task destination to be identifiable.

**Preserve / do not design:** no new summary field, dashboard-side authority, task status transition, or invented Thread/Job record. Do not add a row for a count without a record.

**Acceptance evidence:** deterministic fixtures for `0/[]`, `1/[row]`, and `1/[]`; browser proof that TODAY and the card agree in each; keyboard activation reaches the current task record/action; loading, error, 401/403, and stale treatment remain truthful.

## #574 — Artifact card time and provenance

**Target route and states:** `/orgs/:slug/artifacts`, artifact cards for convention-matching and neutral filenames, with valid and unavailable `modified_at` values.

**Layout and interaction:** retain the card hierarchy: type/thumbnail, title, optional provenance line, then file metadata/actions. Render a labeled modified time from the existing `modified_at` field (for example, `Modified Aug 5, 2026`); it is file modification time, not authoring time. Render agent/thread/date provenance only when it is deterministically derived from the documented filename convention, and keep its derived nature visually secondary. For a neutral filename, omit provenance rather than displaying a bare `—`. For unavailable/invalid modification time, show `Modified time unavailable`; do not make up a time or source.

**Component/token mapping:** existing `ArtifactCard`, `IdBadge`, file type icon/pill, download/delete controls, semantic muted text, and mono treatment for technical file metadata. Use a `Tooltip` only to clarify a derived filename convention, never as the sole unavailable-state label.

**Accessibility and responsive:** make the modified label readable as text, preserve the current linked thread accessible name, and ensure metadata wraps above actions without overlap. Icon-only delete keeps its programmatic label.

**Preserve / do not design:** no new server-captured author/thread/status fields, artifact-detail route, diff/review history, or claim that filename-derived data is authoritative.

**Acceptance evidence:** screenshot and browser assertions for (a) supplied `modified_at` plus filename provenance, (b) supplied `modified_at` with neutral filename, and (c) unavailable time; verify the visible label distinguishes modified versus derived provenance and all existing download/delete contracts remain intact.

## #575 — Assistant executor registration semantics

**Target route and states:** `/orgs/:slug/settings/assistant`, specifically the `Register executor` / `Switch executor` card in configured, uninitialized, pending, validation-error, executable-not-found, success, stale/broken, and 401/403 states.

**Layout and interaction:** directly below the existing explanation of workspace derivation and single-active-executor replacement, add concise status copy: **“Registration applies immediately; no daemon restart is required.”** Keep the existing separate reconfigure explanation: reconfigure closes open assistant sessions and clears saved configuration before a new registration. On success, refresh the status card in place; do not add a restart button, a fake progress phase, or a second configuration home.

**Component/token mapping:** existing `AssistantSection`, status card/badge, `Select`, `Input`, `Label`, `Button`, `Dialog`, alert text, and semantic positive/danger/muted tokens. Success and failure need text in addition to status color.

**Accessibility and responsive:** associate the live-applied note with the form; keep field-level errors and alert text announced, registration pending state named, confirmation dialog focus-trapped, and controls operable without horizontal scrolling.

**Preserve / do not design:** no changes to daemon authentication, executor permissions, registration payload, workspace derivation, or reconfigure/session semantics.

**Acceptance evidence:** deterministic registration success shows the updated configured state without a restart instruction; invalid command and executable-not-found show server-sourced recovery text; keyboard-only form, dialog, error, and 401/403 captures in both themes.

## #576 — Work Hours operating control placement

**Target routes and states:** the new Settings → Organization → Operating controls view and existing `/orgs/:slug/work-hours`, `/orgs/:slug/work-hours?view=wakes`, and `/orgs/:slug/work-hours/:agent`. Cover enabled/disabled, eligible/ineligible, no-routine, scheduled, fired, terminal-error, read failure, validation rejection, and stale/disconnected states.

**Layout and interaction:** Settings becomes the one editable owner for the organization-wide gate and eligibility policy. Its control shows organization scope, effective state, eligibility impact preview, explicit warning before disabling, valid-save “effective at the next scheduler pass” feedback, field/error summary, last-known-good recovery, and a Work Hours link. Work Hours retains its wider domain workspace, schedule tier/provenance, routine source, wakes, root-task links, and a derived-state banner with a **Manage operating control** deep link. The overview/detail must never expose a second editable gate or eligibility control.

**Component/token mapping:** existing Settings sub-navigation/content panel, Work Hours tabs, `ProvenanceBadge`, roster/impact rows, `FormField`/`Label`/`Select`, `Dialog`, `Button`, `StatusBadge`, `IdBadge`, and semantic attention/danger/positive/info tokens. Use existing destructive-confirmation behavior for disable.

**Accessibility and responsive:** settings and Work Hours deep links work in both directions; keyboard focus reaches impact preview, confirm/cancel, validation summary, server-error recovery, tabs, and root-task links. Pair enabled/disabled and eligible/ineligible colors with text; preserve operability without a mobile shell redesign.

**Preserve / do not design:** submit only the existing organization-settings patch containing `working_hours`; keep the gate → eligibility → org/team/agent resolution → routine → evidence order, atomic validation/last-known-good behavior, audit evidence, one-slot/one-wake behavior, and current authority. No new endpoint/store, per-agent enablement, scheduler/routine/catch-up/calendar/override control, or Settings → System relocation.

**Acceptance evidence:** desktop light/dark frames of both owner and handoff surfaces; interaction frames for impact, disable confirmation, valid save, invalid agent/team reference, and recovered prior configuration; contract/browser proof of the existing patch shape and `config:working_hours` audit evidence; fixtures for every named effective/wake outcome; keyboard, loading/empty/error/401/403/stale proof.

## #577 — Intent-first navigation pilot

**Target routes and states:** only the Founder-approved normal AppShell pilot routes. The crosswalk starts from current destinations: Dashboard, Threads, Tasks, Jobs, Todos; Agents, Teams, Work Hours; Skills, Settings; KB, Artifacts, Audit, Dreams, Usage, Health. Existing route paths, `/schedule` → Work Hours Wakes, and `/spend` → Usage remain compatibility targets.

**Layout and interaction:** express the selected group as a text heading plus navigational structure, not a color-only sidebar treatment. Retain selected organization context and make current destination/group discoverable. Dashboard, assistant, summary cards, and evidence surfaces visibly hand off to their canonical record; they do not imply an action happened inside the triage surface. Command palette, help, top bar, and route highlight must stay aligned with the chosen crosswalk. Design the smallest founder-approved group/surfaces first; do not render all groups as an all-at-once shell replacement.

**Component/token mapping:** existing `AppShell`, `Sidebar`, `AppBar`/`TopBar`, `ContentWrap`, current navigation item/focus style, `CommandPalette`, `HelpSheet`, `IdBadge`, `StatusBadge`, and semantic shell/surface/type/spacing tokens. Reuse card/row patterns from the changed pilot families; no new navigation design system.

**Accessibility and responsive:** semantic primary navigation and group labels; active route/group communicated with text and `aria-current`-appropriate semantics; keyboard path through organization switcher, navigation, command/help, and canonical handoffs; visible focus and non-color selected state. Desktop is the supported target; report, rather than conceal, narrow-width limitations.

**Preserve / do not design:** no path migration, responsive-shell promise, hidden/deleted normal route, novel record fields, role/RBAC model, assistant governance write, B2 custom-Skills behavior change, Work Hours semantic change, or prototype/developer route promotion.

**Acceptance evidence:** a route-to-group/deep-link crosswalk for every changed route; before/after 1440×900 light/dark captures; deterministic checks for legacy redirects and selected-route highlighting; browser evidence from each changed triage/evidence surface to its canonical record; loading, empty, populated, stale/disconnected, failure, and 401/403 frames for each changed family.

## Handoff checklist for Claude Design

For the selected issue, return annotated frames and a short evidence ledger that identifies the route, fixture/state, reused component/token, visible source/derived/unavailable treatment, keyboard/focus behavior, and preserved contract. Escalate instead of designing around a request for a new API field, authority rule, scheduler behavior, or mobile shell.
