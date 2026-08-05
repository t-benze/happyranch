# System-surface visual design handoff

| Field | Value |
| --- | --- |
| Status | design handoff — implementation remains gated |
| Owner | Product Lead |
| Date | 2026-08-05 |
| Source Links | [System-surface Redesign PRD](prds/2026-08-05-system-surface-redesign-prd.md); [PR #555](https://github.com/t-benze/happyranch/pull/555); TASK-4373 evidence; focused fidelity review TASK-4377; [#572](https://github.com/t-benze/happyranch/issues/572)–[#577](https://github.com/t-benze/happyranch/issues/577) |
| Commitment Boundary | analysis-only design translation. This document neither authorizes a UI build nor changes routes, API contracts, scheduler behavior, browser authority, or delivery timing. |
| Founder rulings | THR-140 approves #576 with Settings → Organization as the sole editable owner for Work Hours enablement and eligibility, and approves #577 as a navigation-only AppShell grouping pilot after #576. Each remains separate implementation work subject to its own PR and review. |

## Purpose and use

This is the visual and interaction handoff for the agreed system-surface redesign
and its fidelity fixes. It translates the PRD into reviewable design constraints;
it is not a replacement product spec. The current runtime and the PRD remain the
authority where a prototype, mock, or visual preference conflicts with a stored
record or server contract.

Design and engineering should use this document only with the linked issue that
owns the selected change:

| Work | Owner issue | Scope boundary |
| --- | --- | --- |
| Work Hours organization-control placement | [#576](https://github.com/t-benze/happyranch/issues/576) | First Founder-approved pilot; one existing org-settings patch and one mutable representation. |
| Intent-first navigation pilot | [#577](https://github.com/t-benze/happyranch/issues/577) | Founder-approved after #576; a bounded navigation-only pilot that must not become a route migration or visual-system rewrite. |
| Dreams unavailable-data defect | [#572](https://github.com/t-benze/happyranch/issues/572) | Truthful fallback only. |
| Dashboard attention contradiction | [#573](https://github.com/t-benze/happyranch/issues/573) | Traceability to a canonical record or removal of the unsupported claim. |
| Artifact provenance placeholder | [#574](https://github.com/t-benze/happyranch/issues/574) | Show supplied provenance or an explicit unavailable treatment. |
| Executor restart/live semantics | [#575](https://github.com/t-benze/happyranch/issues/575) | Copy follows confirmed runtime behavior; no inferred semantics. |

No additional issue is created for generic “polish.” New visual requests must map
to a named issue and a PRD requirement, or enter separate triage.

## System-surface information architecture

The future navigation groups are information-scent labels, not new route names.
Every current normal AppShell route and deep link remains valid unless a separately
approved migration changes it. The selected organization and active group must be
perceivable through text and structure, not color alone.

| Group | Destinations | Design rule |
| --- | --- | --- |
| Operate | Dashboard, Threads, Tasks, Jobs, Todos | Dashboard is triage. Cards and assistant responses link to canonical records; they do not imply that they performed a record action. |
| Organization | Agents, Teams, Work Hours | Explain membership, ownership, cadence, source-of-value, and current effective state. |
| Govern | Skills, Settings | Skills retains its lifecycle ledger. Settings owns organization/runtime operating controls. Do not create browser-only authorization signals. |
| Evidence | KB, Artifacts, Audit, Dreams, Usage, Health | Present source, time/window, freshness or an explicit unavailable/derived state; route consequential action to the canonical record. |
| Global | Organization switcher, command palette, help, assistant dock, account/runtime context | Available across normal AppShell routes. The assistant may navigate and show evidence; it has no direct governance-write control. |

The current sidebar grouping is a baseline implementation detail, not approval to
silently relabel or regroup all destinations. The #577 route-to-group crosswalk
must precede any navigation change.

## Work Hours: governance control and domain context

The organization-wide Work Hours gate and eligibility policy belong in
**Settings → Organization → Operating controls**. Work Hours remains the domain
workspace: effective schedule/provenance, tier configuration, read-only routine
source, next wakes, history, and root-task linkage.

| Surface | Must show | May mutate | Must not show or change |
| --- | --- | --- | --- |
| Settings → Organization → Operating controls | Organization scope; effective enabled state; eligibility impact preview; confirmation before disabling; server validation/rejection; last-known-good recovery; “effective at next scheduler pass” feedback; link to Work Hours. | The one working_hours section via the existing organization settings patch. | A daemon-global switch, system-setting substitute, per-agent switch, separate endpoint/store, client-side authority claim. |
| Work Hours overview/detail | Derived gate and eligibility reason; schedule tier and source-of-value; no-routine/disabled/ineligible explanation; “Manage operating control” deep link. | Existing supported tier edits only, if preserved by the approved implementation plan. | A second editable gate/eligibility representation or divergent effective state. |
| Wakes | Scheduled/fired/terminal-error evidence and spawned root-task links. | None newly introduced. | Routine authoring, catch-up policy controls, calendar/holiday/override UI, or a claim that a wake is a Todo/task/thread. |

The control must preserve the server order: gate → eligibility → organization/team/agent
leaf resolution → routine source → wake evidence. Client-computed display values are
not scheduler authority. Invalid agent/team references must leave the previous valid
configuration effective and retain the existing config:working_hours audit evidence.

## Desktop and mobile states

Desktop is the supported design target. Deliver design frames at 1440×900 in light
and dark themes for each changed surface, plus the named interaction overlays below.
The layout should preserve the existing shell’s durable context (organization,
navigation, page title, assistant entry) and make the canonical record handoff clear.

| State family | Required design treatment |
| --- | --- |
| Loading | Preserve page title/context; use the established skeleton/loading pattern; do not present pending values as current facts. |
| Empty | Explain why data is absent, distinguish “no records” from unavailable data, and give only a supported next step. |
| Populated | Maintain readable scan hierarchy, explicit source/value labels where applicable, and canonical record links. |
| Stale/disconnected | Retain last-known rendered data only with a visible stale/disconnected cue and recovery path; no freshness guarantee. |
| Validation rejection | Tie errors to the field and an error summary, state that the prior valid configuration remains effective, and retain the attempted context. |
| 401/403 | Describe session/auth failure or server denial truthfully; do not say a visual “founder-only” gate enforced access. |
| Recoverable error | Keep context, offer retry/recovery, and never replace a record identifier with fabricated content. |

Mobile is explicitly **not** a responsive-shell deliverable in this pilot. At narrow
widths, current content must remain operable without horizontal clipping, lost focus,
or unreachable dialogs/actions; a redesigned drawer, collapsed navigation, or
mobile-specific IA requires separately approved scope. Any limitation discovered in
review is recorded as a limitation, not treated as solved by desktop screenshots.

## Provenance, authority, and accessibility requirements

Every prominent claim must have one of three treatments: (1) a named source and
time/window, (2) an explicit derived/source-of-value label, or (3) an unavailable
state. This applies especially to evidence cards, dashboard summaries, health/usage
projections, Dreams counts, and Work Hours effective configuration.

- Never render undefined, NaN, a placeholder dash, or an empty metric as a factual value. Use a named unavailable state when no contract supplies the value.
- Keep raw configuration, effective configuration, and provenance distinguishable. Explain that scheduler-side resolution is authoritative where Work Hours computes a display projection.
- Keep browser-visible controls honest: browser labels do not establish a human role; server 401/403 results remain decisive. No assistant approval, Skills direct catalog write, or governance write path may be designed by implication.
- Use semantic landmarks (header, primary nav, main, complementary context, dialogs) and programmatic labels for icon-only controls.
- Provide keyboard access and visible focus to group navigation, organization selection, command/help, deep links, controls, dialogs, validation feedback, and recovery actions. Return focus after closing overlays.
- Pair every color-coded state with text, icon, or pattern. Status badges, enabled/disabled state, warnings, and selected navigation cannot rely on color alone.
- Meet the existing token contrast intent in both themes; do not introduce raw hex/OKLCH values or one-off visual effects to solve a surface-level problem.

## Design-token and component mapping

The delivery must compose the existing design system rather than recreate it. This
table is an explicit review contract; a proposed new primitive/pattern belongs in a
separate design-system review with reuse evidence.

| Need | Use existing token/component | Mapping expectation |
| --- | --- | --- |
| Shell and active destination | Sidebar, AppBar, AppShell; --spacing-rail, semantic canvas/sunken/raised surfaces | Group labels, active route, organization context, and navigation focus use the shared shell. Do not add a second shell or hard-code widths/colors. |
| Page hierarchy and canonical handoff | PageHeader, ContentWrap, Button, IdBadge, StatusBadge | Use heading/meta/action slots; record IDs and status use existing patterns, and triage links visibly lead to the canonical record. |
| Settings and Work Hours forms | FormField, Label, Input, Select, Tabs/SubTabBar, Dialog, Button | Validation, confirmation, and focus restoration must use these primitives; a destructive disable action needs its existing confirmation pattern. |
| Empty, unavailable, and error | EmptyState; semantic text-muted, attention, danger, info and soft companion tokens | “Unavailable,” “derived,” “stale,” and error are explicit textual states. Do not use an em dash as the sole provenance treatment. |
| State, provenance, and eligibility | StatusBadge; semantic positive/attention/danger/info tokens; text-mono-* | Keep status distinct from role identity. Pair an eligibility/state label with source/provenance text; use mono only for IDs/timestamps/technical values. |
| Evidence lists and attention queue | Existing cards/rows plus IdBadge, StatusBadge, Sparkline only when supplied data supports it | #573 must expose a routable record or remove the “waiting” assertion. #574 must show source/time only when supplied. |
| Overlay and help behavior | Dialog, Drawer, Tooltip, HelpSheet, CommandPalette | Use established focus trap, Escape, labels, and return-focus behavior; tooltips do not replace accessible names. |

Use semantic tokens from web/src/design-system/tokens/tokens.css: surface, text,
border, accent, attention, info, danger, positive, spacing, radii, type, and
shadow tokens. Preserve light/dark token parity. Existing backwards-compatibility
aliases may remain during an implementation, but new compositions should choose the
semantic vocabulary rather than a raw or arbitrary value.

## Design-review deliverables and acceptance evidence

Before an implementation PR is reviewed, attach a concise mapping from every
selected issue acceptance criterion to proof. At minimum, provide:

1. Desktop light/dark frames at 1440×900 for the changed owner and handoff surfaces; for #576, Settings → Organization and Work Hours overview/detail.
2. Interaction frames for Work Hours impact preview, disable confirmation, valid save/next-pass feedback, invalid-reference recovery, derived disabled/ineligible explanation, and the deep link in both directions.
3. State frames for loading, empty, populated, stale/disconnected, validation failure, 401/403, and recoverable error; state which fixtures or contract responses substantiate each.
4. A route/deep-link and component/token crosswalk. It must identify reused primitives/patterns and explain any proposed addition.
5. Keyboard and screen-reader annotations: landmarks, accessible names, focus order, focus return, and non-color equivalents.
6. A provenance ledger for every visible metric/status: source field or record, time/window/freshness treatment, derived calculation (if any), and unavailable fallback.

## Explicit exclusions

This handoff does not approve a wholesale visual rewrite, new design tokens or a
design-system rebuild, route-path migration, responsive-shell work, new metrics or
record fields, role/RBAC changes, assistant governance writes, Skills lifecycle
changes, direct catalog writes, Work Hours scheduler/eligibility/resolution/routine
changes, calendar/override/routine-authoring controls, cost/SLA/retention claims,
or prototype-only fields. Those require a new product/engineering proposal.
