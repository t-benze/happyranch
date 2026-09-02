# HappyRanch web design system

This is the normative contract for reusable UI under `src/design-system/`.
Feature compositions keep using the stable `@/design-system/...` import paths.

## Tokens and boundaries

- `src/design-system/tokens/tokens.css` is the sole source of raw colors.
  Components and stories consume semantic utilities; raw hex values elsewhere
  are rejected by `scripts/verify-design-system.sh`.
- Primitives wrap basic interaction and Radix behavior. Patterns combine them
  into reusable product language. Layouts own reusable geometry.
- Components are pure props-in/events-out unless a documented role requires an
  isolation-safe local provider. Reuse them instead of feature-local copies.

## Storybook is the catalogue

Storybook supersedes the former committed JSON registry and in-app
`/__design__` route. There is no generated catalogue, registry generator,
route flag, daemon endpoint, MCP transport, or on-disk agent contract.
`npm run dev` and `npm run build` build only the product SPA.

```bash
npm run storybook
npm run build-storybook
```

The deterministic static output is `web/storybook-static/`, a local CI
artifact rather than a hosted service. Chromatic and similar services are not
used. `.storybook/main.ts` uses React-Vite, the application `@` alias, and
Essentials for controls/docs. `.storybook/preview.tsx` imports `src/styles.css`
and supplies only `MemoryRouter`, a network-disabled Query client, and
`TooltipProvider`. Stories use canned data and never contact a live daemon.
The theme toolbar applies the same `data-theme` contract as the app; Foundations
shows semantic color and typography tokens in light and dark.

## Authoring and enforced coverage

Use CSF `*.stories.tsx`. Each new reusable `.tsx` file in `primitives/`,
`patterns/`, or `layouts/` needs either:

1. a named exported story beginning with the component name and meaningful
   representative states; or
2. a component-specific technical exclusion below explaining why isolation is
   unsafe/misleading and where the behavior is covered instead.

Use controls/autodocs for real public variants. Interaction stories must be safe
to click/type. Add loading, empty, error, populated, auth, and permission states
only where the reusable unit owns them. `storybook-coverage.test.ts` discovers
component sources, named stories, and explicit exclusion markers, failing on
new uncovered units without creating another generated registry.

## Coverage ledger

**44 reusable components: 37 story-covered, 7 justified exclusions.** Stories
preserve the former catalogue's descriptions, examples, variants, and token
visibility through titles, docs, controls, and representative renders.

| Reusable export | Story or exclusion | Representative states / variants | Tokens |
|---|---|---|---|
| `Button` | Primitives / Button States | default, disabled; variant/size controls | `components.button` |
| `Dialog` | Primitives / Dialog Interaction | trigger → portal | `components.dialog` |
| `Drawer` | Primitives / Drawer Interaction | trigger → drawer | `components.drawer` |
| `DropdownMenu` | Primitives / Dropdown Menu Interaction | trigger, populated actions | `components.dropdown_menu` |
| `Input` | Primitives / Input States | empty, populated, disabled | `components.input` |
| `Label` | Primitives / Label State | associated label | `components.label` |
| `Select` | Primitives / Select Interaction | selected/open options | `components.select` |
| `SubTabBar` | Primitives / Sub Tab Bar States | active/inactive navigation | `components.subtabbar` |
| `Tabs` | Primitives / Tabs Variants | pills, underline, segmented | `components.tabs` |
| `Textarea` | Primitives / Textarea States | empty, populated/disabled | `components.textarea` |
| `Tooltip` | Primitives / Tooltip Interaction | open, hover/focus | `components.tooltip` |
| `AgentChip` | Patterns / Agent Chip Roles | founder, manager, worker | `components.agent_chip` |
| `AuditRow` | [excluded:AuditRow] Requires complete daemon `AuditEntry`; canonical data/density behavior is in `AuditRow.test.tsx` and Audit feature tests. | comfortable/compact in tests | `components.audit_row` |
| `CommandPalette` | Patterns / Command Palette Populated | open, populated, searchable | `components.dialog` |
| `Composer` | Patterns / Composer States | ready, abort, error/draft | input/button tokens |
| `CrescentMoonBadge` | Patterns / Crescent Moon Badge State | present | `components.badge` |
| `EmptyState` | Patterns / Empty State With Action | empty with CTA | `components.empty_state` |
| `FilterSidebar` | Patterns / Filter Sidebar Interaction | all/selected, counts | `components.filter_sidebar` |
| `FormField` | Patterns / Form Field States | normal, error | input/label tokens |
| `HelpSheet` | Patterns / Help Sheet Interaction | open shortcuts | dialog/kbd tokens |
| `IdBadge` | Patterns / Id Badge Kinds | thread, task | `components.badge` |
| `InboxRow` | Patterns / Inbox Row States | active/open, archived/thread | `components.inbox_row` |
| `KbdChip` | Patterns / Kbd Chip Combinations | key, chord | `components.kbd_chip` |
| `Markdown` | Patterns / Markdown Content | heading/list/emphasis/code | typography/code |
| `MentionAutocomplete` | Patterns / Mention Autocomplete Populated | populated portal/listbox | surface/border |
| `MentionTextarea` | Patterns / Mention Textarea Interaction | editable mention | `components.textarea` |
| `Mermaid` | Patterns / Mermaid Diagram | loading → diagram | `components.code_block` |
| `MessageBubble` | Patterns / Message Bubble Variants | founder, worker, system | `components.message_bubble` |
| `PageHeader` | Patterns / Page Header With Actions | metadata/action | heading/caption |
| `RecipientsInput` | Patterns / Recipients Input Interaction | editable prefix | mention/surface |
| `Sparkline` | Patterns / Sparkline Variants | default/green/yellow/red | semantic tiers |
| `StatValue` | Patterns / Stat Value Formats | token/count, right/inline | `components.stat_value` |
| `StatusBadge` | Patterns / Status Badge States | all lifecycle states | `components.badge` |
| `TaskCard` | [excluded:TaskCard] Requires daemon-owned `TaskRecord` navigation semantics; canonical populated/density states are in Tasks feature tests. | comfortable/compact in tests | badge/card |
| `ThreadHeader` | Patterns / Thread Header States | open/dream/action, archived | thread layout |
| `TraceTree` | [excluded:TraceTree] Requires structurally valid audit trace/cost maps; recursion/density are in `TraceTree.test.tsx`. | comfortable/compact in tests | `components.trace_tree` |
| `TypingBubble` | Patterns / Typing Bubble States | working, queued | info/muted |
| `AppBar` | [excluded:AppBar] Reads live shell/org/navigation contexts and hosts product commands; AppShell/route tests cover it. | shell context in tests | topbar/grid |
| `ErrorBoundary` | [excluded:ErrorBoundary] Lifecycle capture/reset is not a static catalogue unit; component and route tests cover error/recovery. | normal/error/reset in tests | feedback |
| `Sidebar` | [excluded:Sidebar] Reads org/route/responsive/navigation contexts; `Sidebar.test.tsx` and AppShell tests cover it without misleading canned permissions. | desktop/mobile/navigation in tests | sidebar/grid |
| `TopBar` | [excluded:TopBar] Reads prototype/org route state; prototype/AppShell tests cover its complete shell contract. | shell context in tests | topbar/grid |
| `ContentWrap` | Layouts / Content Wrap Responsive | bounded responsive content | layout content/wrap |
| `DashboardLayout` | Layouts / Dashboard Layout Populated | four populated slots | layout grid |
| `ThreadsLayout` | Layouts / Threads Layout Populated | inbox/detail columns | threads grid |

## Frontend readiness matrix

| State | Applicability / evidence |
|---|---|
| Interaction | Applicable: portal/menu/select/tabs/filter/composer/mention stories are safe locally. |
| Loading | Applicable only to Mermaid's local render transition; backend loading is N/A to pure units. |
| Empty | Applicable to EmptyState and blank input examples. |
| Error | Applicable to FormField validation and Composer draft-preserving error. |
| Populated | Applicable across all three layers. |
| Auth | N/A: reusable units do not own authentication; context consumers are excluded and app-tested. |
| Permission | N/A: feature/shell owners authorize before passing props; context consumers are excluded. |

## Deterministic verification and acceptance

`scripts/verify-design-system.sh` runs typecheck, lint, unit coverage, the static
Storybook build, and raw-hex enforcement. `scripts/local_ci.sh web|all` and the
GitHub Web gate explicitly run SPA and Storybook builds once each. Storybook is
not a product prebuild hook, preventing duplicate builds.

- [ ] Stable imports and shipped component behavior remain unchanged.
- [ ] No `/__design__`, route flag, registry, generator, freshness hook, or stale metadata remains.
- [ ] Every reusable unit has meaningful discoverable coverage or a justified exclusion.
- [ ] Autodocs/controls, semantic tokens, themes, and safe local providers work.
- [ ] No live daemon, hosted visual service, or product behavior is introduced.
- [ ] Lint, typecheck, unit tests, SPA build, Storybook build, design-system verification, browser evidence, and Node 24 local CI pass.
