# HappyRanch web design system

This is the normative contract for reusable UI under `src/design-system/`.
Feature compositions keep using the stable `@/design-system/...` import paths.

## Tokens and boundaries

- `src/design-system/tokens/tokens.css` is the source of new raw colors.
  Components and stories consume semantic utilities; the branch-aware check in
  `scripts/verify-design-system.sh` rejects newly added CSS-like hex values
  elsewhere without confusing issue references such as `#302` for colors.
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

1. an explicit `[story:path#Export]` ledger mapping to an exported CSF story
   that imports and renders that exact component with meaningful states; or
2. a component-specific technical exclusion below explaining why isolation is
   unsafe/misleading and where the behavior is covered instead.

Use controls/autodocs for real public variants. Interaction stories must be safe
to click/type. Add loading, empty, error, populated, auth, and permission states
only where the reusable unit owns them. `storybook-coverage.test.ts` discovers
component sources and parses this authoritative ledger, then proves each story
mapping imports and renders its exact component through reachable JSX, a locally
resolvable helper actually called from the render return path, or the exact CSF
component field. It rejects uncalled helper bodies, metadata, args, docs, unrelated
values, and name-only placeholders while another component renders. New sources,
missing exports, stale mappings, and unjustified overlaps therefore fail without
creating another generated registry or catalogue artifact.

## Coverage ledger

**44 reusable components: 40 story-covered, 4 justified exclusions.** Stories
preserve the former catalogue's descriptions, examples, variants, and token
visibility through titles, docs, controls, and representative renders.

| Reusable export | Story or exclusion | Representative states / variants | Tokens |
|---|---|---|---|
| `Button` | [story:primitives/Primitives.stories.tsx#ButtonStates] Primitives / Button States | default, disabled; variant/size controls | `components.button` |
| `Dialog` | [story:primitives/Primitives.stories.tsx#DialogInteraction] Primitives / Dialog Interaction | trigger → portal | `components.dialog` |
| `Drawer` | [story:primitives/Primitives.stories.tsx#DrawerInteraction] Primitives / Drawer Interaction | trigger → drawer | `components.drawer` |
| `DropdownMenu` | [story:primitives/Primitives.stories.tsx#DropdownMenuInteraction] Primitives / Dropdown Menu Interaction | trigger, populated actions | `components.dropdown_menu` |
| `Input` | [story:primitives/Primitives.stories.tsx#InputStates] Primitives / Input States | empty, populated, disabled | `components.input` |
| `Label` | [story:primitives/Primitives.stories.tsx#LabelState] Primitives / Label State | associated label | `components.label` |
| `Select` | [story:primitives/Primitives.stories.tsx#SelectInteraction] Primitives / Select Interaction | selected/open options | `components.select` |
| `SubTabBar` | [story:primitives/Primitives.stories.tsx#SubTabBarStates] Primitives / Sub Tab Bar States | active/inactive navigation | `components.subtabbar` |
| `Tabs` | [story:primitives/Primitives.stories.tsx#TabsVariants] Primitives / Tabs Variants | pills, underline, segmented | `components.tabs` |
| `Textarea` | [story:primitives/Primitives.stories.tsx#TextareaStates] Primitives / Textarea States | empty, populated/disabled | `components.textarea` |
| `Tooltip` | [story:primitives/Primitives.stories.tsx#TooltipInteraction] Primitives / Tooltip Interaction | open, hover/focus | `components.tooltip` |
| `AgentChip` | [story:patterns/Patterns.stories.tsx#AgentChipRoles] Patterns / Agent Chip Roles | founder, manager, worker | `components.agent_chip` |
| `AuditRow` | [story:patterns/Patterns.stories.tsx#AuditRowDensity] Patterns / Audit Row Density | complete audit/job fixtures; comfortable, compact, expandable | `components.audit_row` |
| `CommandPalette` | [story:patterns/Patterns.stories.tsx#CommandPalettePopulated] Patterns / Command Palette Populated | open, populated, searchable | `components.dialog` |
| `Composer` | [story:patterns/Patterns.stories.tsx#ComposerStates] Patterns / Composer States | ready, abort, error/draft | input/button tokens |
| `CrescentMoonBadge` | [story:patterns/Patterns.stories.tsx#CrescentMoonBadgeState] Patterns / Crescent Moon Badge State | present | `components.badge` |
| `EmptyState` | [story:patterns/Patterns.stories.tsx#EmptyStateWithAction] Patterns / Empty State With Action | empty with CTA | `components.empty_state` |
| `FilterSidebar` | [story:patterns/Patterns.stories.tsx#FilterSidebarInteraction] Patterns / Filter Sidebar Interaction | all/selected, counts | `components.filter_sidebar` |
| `FormField` | [story:patterns/Patterns.stories.tsx#FormFieldStates] Patterns / Form Field States | normal, error | input/label tokens |
| `HelpSheet` | [story:patterns/Patterns.stories.tsx#HelpSheetInteraction] Patterns / Help Sheet Interaction | open shortcuts | dialog/kbd tokens |
| `IdBadge` | [story:patterns/Patterns.stories.tsx#IdBadgeKinds] Patterns / Id Badge Kinds | thread, task | `components.badge` |
| `InboxRow` | [story:patterns/Patterns.stories.tsx#InboxRowStates] Patterns / Inbox Row States | active/open, archived/thread | `components.inbox_row` |
| `KbdChip` | [story:patterns/Patterns.stories.tsx#KbdChipCombinations] Patterns / Kbd Chip Combinations | key, chord | `components.kbd_chip` |
| `Markdown` | [story:patterns/Patterns.stories.tsx#MarkdownContent] Patterns / Markdown Content | heading/list/emphasis/code | typography/code |
| `MentionAutocomplete` | [story:patterns/Patterns.stories.tsx#MentionAutocompletePopulated] Patterns / Mention Autocomplete Populated | populated portal/listbox | surface/border |
| `MentionTextarea` | [story:patterns/Patterns.stories.tsx#MentionTextareaInteraction] Patterns / Mention Textarea Interaction | editable mention | `components.textarea` |
| `Mermaid` | [story:patterns/Patterns.stories.tsx#MermaidDiagram] Patterns / Mermaid Diagram | loading → diagram | `components.code_block` |
| `MessageBubble` | [story:patterns/Patterns.stories.tsx#MessageBubbleVariants] Patterns / Message Bubble Variants | founder, worker, manager, decline, system | `components.message_bubble` |
| `PageHeader` | [story:patterns/Patterns.stories.tsx#PageHeaderWithActions] Patterns / Page Header With Actions | metadata/action | heading/caption |
| `RecipientsInput` | [story:patterns/Patterns.stories.tsx#RecipientsInputInteraction] Patterns / Recipients Input Interaction | editable prefix | mention/surface |
| `Sparkline` | [story:patterns/Patterns.stories.tsx#SparklineVariants] Patterns / Sparkline Variants | default/green/yellow/red | semantic tiers |
| `StatValue` | [story:patterns/Patterns.stories.tsx#StatValueFormats] Patterns / Stat Value Formats | token/count, right/inline | `components.stat_value` |
| `StatusBadge` | [story:patterns/Patterns.stories.tsx#StatusBadgeStates] Patterns / Status Badge States | all lifecycle states | `components.badge` |
| `TaskCard` | [story:patterns/Patterns.stories.tsx#TaskCardDensity] Patterns / Task Card Density | populated/active; comfortable, compact; injected routes | badge/card |
| `ThreadHeader` | [story:patterns/Patterns.stories.tsx#ThreadHeaderStates] Patterns / Thread Header States | open/dream/action, archived | thread layout |
| `TraceTree` | [story:patterns/Patterns.stories.tsx#TraceTreeDensity] Patterns / Trace Tree Density | recursive cost fixture; comfortable, compact | `components.trace_tree` |
| `TypingBubble` | [story:patterns/Patterns.stories.tsx#TypingBubbleStates] Patterns / Typing Bubble States | working, queued | info/muted |
| `AppBar` | [excluded:AppBar] Reads live shell/org/navigation contexts and hosts product commands; AppShell/route tests cover it. | shell context in tests | topbar/grid |
| `ErrorBoundary` | [excluded:ErrorBoundary] Lifecycle capture/reset is not a static catalogue unit; component and route tests cover error/recovery. | normal/error/reset in tests | feedback |
| `Sidebar` | [excluded:Sidebar] Reads org/route/responsive/navigation contexts; `Sidebar.test.tsx` and AppShell tests cover it without misleading canned permissions. | desktop/mobile/navigation in tests | sidebar/grid |
| `TopBar` | [excluded:TopBar] Reads prototype/org route state; prototype/AppShell tests cover its complete shell contract. | shell context in tests | topbar/grid |
| `ContentWrap` | [story:layouts/Layouts.stories.tsx#ContentWrapResponsive] Layouts / Content Wrap Responsive | bounded responsive content | layout content/wrap |
| `DashboardLayout` | [story:layouts/Layouts.stories.tsx#DashboardLayoutPopulated] Layouts / Dashboard Layout Populated | four populated slots | layout grid |
| `ThreadsLayout` | [story:layouts/Layouts.stories.tsx#ThreadsLayoutPopulated] Layouts / Threads Layout Populated | inbox/detail columns | threads grid |

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
Storybook build, and no-new-raw-hex enforcement. `scripts/local_ci.sh web|all` and the
GitHub Web gate explicitly run SPA and Storybook builds once each. Storybook is
not a product prebuild hook, preventing duplicate builds.

- [ ] Stable imports and shipped component behavior remain unchanged.
- [ ] No `/__design__`, route flag, registry, generator, freshness hook, or stale metadata remains.
- [ ] Every reusable unit has meaningful discoverable coverage or a justified exclusion.
- [ ] Autodocs/controls, semantic tokens, themes, and safe local providers work.
- [ ] No live daemon, hosted visual service, or product behavior is introduced.
- [ ] Lint, typecheck, unit tests, SPA build, Storybook build, design-system verification, browser evidence, and Node 24 local CI pass.
