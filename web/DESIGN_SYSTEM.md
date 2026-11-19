# HappyRanch web design system

This is the normative contract for reusable UI under `src/design-system/`.
Feature compositions keep using the stable `@/design-system/...` import paths.

## Tokens and boundaries

- `src/design-system/tokens/tokens.css` is the source of new raw colors.
- `brand-foreground` is the single semantic foreground for the HappyRanch
  lockup. Its explicit light/dark values are held to at least 4.5:1 against
  the live Sidebar (`bg-surface-sunken`) and Onboarding (`bg-surface-canvas`)
  backgrounds; it is intentionally distinct from the generic UI accent.
  Components and stories consume semantic utilities. The deterministic
  full-production-tree check in `scripts/verify-design-system.sh` rejects raw
  hex, actual default Tailwind palette scales and special colour utilities,
  arbitrary Tailwind colour values,
  CSS `rgb()`/`rgba()`/`hsl()`/`hsla()`/`oklch()`/`oklab()`/`color()` values,
  and complete named CSS colour values outside that authority. Supported
  colour functions are balanced and may contain nested channel functions such
  as `var()`. It recognizes only
  colour-capable utilities, properties, and JSX attributes, so issue references,
  entities, non-colour arbitrary utilities, and token utilities are not hits.
  The exact path/line/column/value/reason baseline is shrinking: moving,
  changing, duplicating, adding, or deleting a listed occurrence fails until
  the baseline is deliberately reconciled in review. A baseline row records
  temporary residue; it does not create visual or token authority.
- Utility classes may only name theme tokens that exist. Tailwind v4 emits no
  CSS for a colour/spacing/radius/shadow utility whose name is not a theme
  token, so `bg-surface-page` silently renders nothing.
  `scripts/verify-design-system.sh --scan-tokens` (also run by the shared
  `verify-design-system-colour-gate.sh` CI gate) fails (exit 4) on any such
  utility in a production class list (string literals and CSS `@apply`; not
  comments or the i18n copy catalogs). A name is defined when a `tokens.css`
  `@theme` block declares it, or when the Tailwind default theme that
  `tokens.css` imports does (for example `max-w-md`), unless a `tokens.css`
  `@theme` block clears that namespace (`--radius-*: initial`,
  `--shadow-*: initial`): then only the `tokens.css` names count, and bare
  `rounded`/`shadow` fail too. Numeric values (`p-4`) are not token names and
  are skipped, but a digit-led scale name in a cleared namespace
  (`rounded-2xl`, `shadow-2xs`) is checked like any other name. Values declared only outside `@theme` (such as
  the dark override block) do not count. Map a missing name to an existing semantic token; add a token only
  when none fits. `scripts/test-design-system-token-scanner.sh` is its fixture
  suite.
- One colour vocabulary (THR-376 PR10). The semantic colour names are
  canonical, and only they hold raw colour values in `tokens.css`:
  `surface-canvas`, `surface-sunken`, `surface-raised`, `surface-overlay`,
  `surface-scrim`, `surface-hover`, `surface-subtle`, `text-primary`,
  `text-secondary`, `text-muted`, `text-inverse`, `text-disabled`,
  `border-default`, `border-strong`, `border-subtle`, `accent-default`,
  `accent-hover`, `accent-soft`, `accent-ring`, `accent-text`,
  `brand-foreground`, `attention`, `attention-soft`, `attention-text`, `info`,
  `info-soft`, `positive`, `danger`, `danger-soft`. Every other `--color-*`
  name (tier, status, feedback, agent, id, shadcn, and the one remaining v3
  name `bg-muted`) is an alias: `var(--color-<semantic>)`, or for an alpha
  tint `color-mix(in oklab, var(--color-<semantic>) 60%, transparent)`, in
  both themes. Aliases are removed scheme by scheme: codemod the utility
  classes to the semantic name, then delete the alias. New code uses the
  semantic names. `--scan-tokens` fails (exit 6) when a
  non-semantic `--color-*` declaration holds a literal or aliases another
  alias. There are no exemptions: the unmapped, unused `tier-slate-tint` was
  deleted rather than kept literal. Alias map (light / dark target; one name
  when both themes agree):

  | Scheme | Alias | Semantic target |
  | --- | --- | --- |
  | v3 (deferred) | `bg-muted` | `surface-sunken` / `surface-raised` |
  | tier | `tier-green` | `positive` |
  | tier | `tier-yellow` | `attention` |
  | tier | `tier-red` | `danger` |
  | tier | `tier-green-tint` | `accent-soft` at 60% |
  | tier | `tier-yellow-tint` | `attention-soft` at 60% |
  | tier | `tier-red-tint` | `danger-soft` at 60% |
  | status | `status-open` | `positive` |
  | status | `status-archiving`, `status-blocked` | `attention` |
  | status | `status-archived` | `text-secondary` / `text-muted` |
  | status | `status-abandoned`, `status-escalated` | `danger` |
  | feedback | `feedback-success` | `positive` |
  | feedback | `feedback-warning` | `attention` |
  | feedback | `feedback-danger` | `danger` |
  | feedback | `feedback-info` | `info` |
  | agent | `agent-manager` | `info` |
  | agent | `agent-worker`, `agent-founder` | `accent-default` |
  | id | `id-thread`, `id-task` | `info` |
  | shadcn | `background` | `surface-canvas` |
  | shadcn | `foreground`, `card-foreground`, `popover-foreground`, `secondary-foreground` | `text-primary` |
  | shadcn | `card`, `secondary` | `surface-raised` |
  | shadcn | `popover` | `surface-overlay` |
  | shadcn | `primary` | `accent-default` |
  | shadcn | `primary-foreground`, `destructive-foreground` | `text-inverse` |
  | shadcn | `muted` | `surface-sunken` |
  | shadcn | `muted-foreground` | `text-muted` |
  | shadcn | `destructive` | `danger` |
  | shadcn | `input` | `border-default` |
  | shadcn | `ring` | `accent-ring` |

  Each alias points at the semantic token whose value it already had, so the
  map is pixel-neutral except two dark values that now follow
  `web/design/pasture-ds.css`. Pasture has no subtle border, so dark
  `border-subtle` is `var(--color-border-default)` (ds.css `--border`; it
  was 0.28). Dark `tier-green-tint` is `accent-soft` at 60% (it was
  `oklch(0.30 0.05 152)`). `tier-green` maps to `positive` (Pasture
  `--positive`), not `accent-default`: the two greens differ in dark by design.
  `surface-hover` and `brand-foreground` stay semantic because each has its
  own value in at least one theme.
- The v3 colour vocabulary is retired (THR-376 PR11). Every utility use of
  `bg`, `bg-subtle`, `bg-raised`, `border`, `fg`, `fg-muted`, `fg-subtle`,
  `accent`, `accent-fg` and the duplicate spellings `surface` and
  `accent-muted` was renamed to the semantic utility its alias resolved to
  (for example background `bg` -> `surface-canvas`, text `fg-muted` ->
  `text-secondary`, border `border` -> `border-default`, `accent` ->
  `accent-default`, `accent-fg` -> `text-inverse`, `surface` ->
  `surface-raised`, `accent-muted` -> `accent-soft`), keeping variants,
  opacity modifiers and important/negative prefixes, and those aliases were
  deleted from both themes. A deleted name is now an unknown token for
  `--scan-tokens` (exit 4). Deferred exception: `bg-muted` stays an alias
  because its light target (`surface-sunken`) and dark target
  (`surface-raised`) differ, so no single rename is pixel-neutral; its two
  uses (ErrorBoundary, SequentialSetup) are unchanged.
- Spacing is Tailwind's linear 4px scale: every numeric step `N` in
  `p-N`/`m-N`/`gap-N`/`space-*-N`/`h-N`/`w-N`/`size-N`/inset/translate and
  the other spacing utilities is `N x 0.25rem` (step 5 = 20px, step 6 = 24px,
  `h-8` = 32px, `h-16` = 64px). There is no t-shirt remap: THR-376 PR4
  removed the old `--spacing-5..8` overrides (24/32/48/64px) and rewrote every
  use to the linear step with the same pixels (5->6, 6->8, 7->12, 8->16).
  `tokens.css` may still declare named spacing tokens (`--spacing-rail`,
  `--spacing-content`, button paddings) and linear-equal `--spacing-0..4`, but
  the `--scan-tokens` gate fails (exit 5) on any numeric `--spacing-N` whose
  value is not `N x 0.25rem`.
- Radius is Pasture only (ds.css 8/12/18/pill): `rounded-sm` 8px,
  `rounded-md` 12px, `rounded-lg` 18px, `rounded-pill` 999px, plus the
  zero radius and Tailwind's static `rounded-full`. `tokens.css` clears
  Tailwind's default radius scale (`--radius-*: initial`, THR-376 PR5), so
  bare `rounded` (was 4px), `rounded-xs` and `rounded-xl`..`rounded-4xl`
  render nothing and the `--scan-tokens` gate fails (exit 4) on them. PR5
  rewrote every use: bare `rounded` to `rounded-sm` (4px to 8px, the Pasture
  small radius) and `rounded-xl` to `rounded-md` (12px, unchanged); there
  were no `rounded-2xl` uses.
- Shadows are the warm Pasture scale only: `shadow-pasture-sm`,
  `shadow-pasture` and `shadow-pasture-lg` (ds.css `--shadow-sm`, `--shadow`,
  `--shadow-lg`), plus the `none` keyword and arbitrary values. `tokens.css` clears
  Tailwind's default shadow scale (`--shadow-*: initial`, THR-376 PR5), so
  bare `shadow` and `shadow-2xs`..`shadow-2xl` render nothing and fail the
  `--scan-tokens` gate. PR5 rewrote every use to the closest Pasture step:
  bare `shadow` and `shadow-sm` to `shadow-pasture-sm`, `shadow-md` to
  `shadow-pasture`, `shadow-lg` and `shadow-xl` to `shadow-pasture-lg`.
  tailwind-merge reads `shadow-pasture-*` as a shadow colour, so `cn()` does
  not drop it in favour of a later `none` shadow; do not override a
  primitive's shadow through `className`.
- Type: `--text-page-title` is Pasture `[data-dir="a"] .h1`, 26px / 500,
  -0.3px tracking, 1.5 line height; pair its `text-*` utility with
  `font-display` (Newsreader) for top-level page titles. `--text-eyebrow` is
  Pasture `.eyebrow`, 11px / 600, 0.07em tracking, 1.5 line height; pair it
  with `uppercase` and `text-text-muted` (ink-3). `--text-display` (30px)
  stays for the Home greeting only.
  Newsreader (`font-display`) is for h1/h2 and display numbers only: row and
  entry titles are Hanken 600 (Knowledge entries: `text-body font-semibold`)
  and small section/rail labels use the `.eyebrow` recipe above, not a serif
  (THR-376 r2 K2/K4).
- `InboxRow`'s `thread` layout uses Hanken 14px / 600
  (`font-sans text-sm font-semibold`); its historical `default` layout stays
  unchanged. Artifact titles use the same Hanken 14px / 600; artifact folder
  paths use JetBrains Mono 13.5px / 600 (`font-mono text-nav font-semibold`).
- `PageTitle` (`patterns/PageTitle.tsx`, THR-376 PR6) is the title block of
  every top-level page (each Sidebar destination's list/index page): an
  optional `eyebrow` (`text-eyebrow uppercase text-text-muted`), exactly one
  `<h1>` (`font-display text-page-title text-text-primary`), an optional
  `lede` (`text-body text-text-secondary`, Pasture `.muted`, phrasing content
  only) and an optional right-aligned `actions` slot that stacks below the
  text under `sm`. Eyebrow, title and lede are 4px apart (`mt-1`). The page
  keeps its own outer wrapper (border, padding, margin below). Render one
  `PageTitle` per page so there is one `<h1>` (exception: Usage keeps an
  `sr-only` h1 instead; see Topbar slots below). The Home greeting is not a
  `PageTitle`: it keeps its own `text-display` h1. Do not pass the h1 classes
  through `cn()`: tailwind-merge reads `text-page-title` as a text colour and
  drops it next to `text-text-primary`. The old `PageHeader` (h2, Hanken
  18/600) is removed; `ThreadHeader` keeps its own h2 detail-pane header.
  Detail, onboarding and story headings are not migrated yet.
- `Card` and `CardHeader` (`primitives/Card.tsx`, THR-376 PR7) are Pasture
  ds.css `.card` / `.card-pad` / `.card-head` / `.card-title`: `bg-surface-raised`
  (--surface), 1px `border-border-default` (--border), `shadow-pasture-sm`
  (--shadow-sm, the warm shadow; Direction A uses the same value in dark) and
  padding 20 (`p-5`). `variant="container"` (default) is 18px (`rounded-lg`,
  ds.css --radius-lg) for a page section or a list; `variant="entry"` is
  12px (`rounded-md`, --radius) for one item in a feed or grid of separate
  cards. ds.css has only the 18px `.card`; the 12px entry radius comes from
  the designer review. `padding="none"` is for cards whose children own the
  padding (a `CardHeader`, a `List`). `as` picks `div`/`section`/`article`/`aside`.
  `CardHeader` is `.card-head`: flex, centred, 10px gap (`gap-2.5`), 16px /
  20px padding, 1px bottom border; `title` is `.card-title` 13px / 600 /
  -0.1px (`text-body-sm font-semibold tracking-[-0.1px]`, an `h3` unless
  `titleAs` says otherwise), `meta` is caption muted text and `actions` are
  pushed right (`.card-head .spacer`). Do not pass the title classes through
  `cn()` (tailwind-merge reads `text-body-sm` as a colour).
- `List`, `Row` and `RowAction` (`patterns/List.tsx`, THR-376 PR7) draw a
  one-card-per-group list: one container `Card` (no padding, clipped) holding
  a `<ul>` whose rows are separated by a 1px `--border` hairline (ds.css
  `.divider`). Use it instead of a stack of separate cards. ds.css has no
  list-row rule: a `Row` (`<li>`) is padded like `.card-head` (16px / 20px,
  `py-4 px-5`). When the whole row is clickable, give the `Row`
  `padding="none"` and put the padding on a `RowAction`, a full-width button
  (or, with `asChild`, the single child such as a router `Link`; only its
  `className` is merged) that hovers to `bg-surface-hover`. Consumer classes
  win, so a dense roster keeps its own padding and selected fill. Adopted on
  Jobs (status groups), the Agents roster and the Dreams feed. The Tasks
  group cards are `Card as="section" padding="none"` because `TaskListRow`
  already draws its own dividers. Other card sites are not migrated yet.
- `SegmentedControl` (`primitives/SegmentedControl.tsx`, THR-376 PR8) is the
  single-select value toggle: pick ONE value from a small fixed set that
  filters or re-groups what is already on the page (time window, group-by,
  status bucket, current/removed view). Controlled (`value` /
  `onValueChange`), generic over string values; options are
  `{ value, label: ReactNode, disabled? }`; `size` is `default` (`px-3 py-1`)
  or `sm` (`px-2.5 py-0.5`, compact card headers). It is a WAI-ARIA radio
  group: `role="radiogroup"` (always pass `aria-label` or
  `aria-labelledby`), `role="radio"` + `aria-checked` options, roving
  tabindex, Arrow keys move and select (Home/End jump), disabled options are
  skipped. A click always calls `onValueChange`, including on the checked
  option. ds.css has no `.seg` rule; the composed values use ds.css tokens
  only: container 1px `--border` (`border-border-default`), `--surface-2`
  fill (`bg-surface-sunken`), `--radius-pill` (`rounded-full`), 4px padding
  and gap; options 12px / 600 (`text-caption font-semibold`, the `.hr-chip` /
  `.btn-sm` label), `--ink-3` at rest and `--ink` on hover; the active option
  is `--accent-soft` + `--accent-text` (`bg-accent-soft text-accent-text`,
  the ds.css `.tag.ok` / Direction-A `.nav-item.active` pair). The label size
  is kept out of `cn()` (tailwind-merge reads `text-caption` as a colour).
  `Tabs variant="segmented"` reads the same class constants.
  **Tabs vs SegmentedControl vs toggles:** use `Tabs` only when each option
  owns a panel (`TabsContent`); a value toggle with no panels is a
  `SegmentedControl`; when several options can be on at once, or clicking
  the active option clears it back to "all" (facet rails, tag pills, legend
  filters, day pickers), keep independent `aria-pressed` toggle buttons.
  Adopted on Tasks (group by), Threads (All/Open/Done), Artifacts (type
  filter), Audit (time window), Health (window), Home top-token threads
  (window), Skills / Custom skills (current/removed) and Todos (status
  filter). A small fixed set of sibling sub-views of one page also uses it
  (THR-376 r2 A12): Work Hours (Overview/Wakes) and Workflows
  (Definitions/Setup/Runs); the caller derives `value` from the URL and
  navigates in `onValueChange`, ignoring a re-pick of the active option.
  The underline `SubTabBar` has no feature callers left (stories only).
- Page-local CSS does not live in `tokens.css`. The Tasks list's `.tasks-*`
  layout rules are in `src/features/tasks/tasks.css`, imported by
  `TasksPage.tsx`; `tokens.css` keeps only the Tasks `--text-task-*` type
  tokens, because Tailwind builds `text-*` utilities only from `@theme` in
  the root stylesheet.
- Mono metadata and tag chips (THR-376 r2 PR-K) use JetBrains Mono 11.5px in
  muted ink: `font-mono text-task-meta text-text-muted`. `text-task-meta` is
  the shared 11.5px size, not a Tasks-only token. Knowledge entry cards list
  tags as one inline meta line (`·` separators bound to the preceding tag
  with a non-breaking space, `wrap-anywhere` so a long unbroken tag wraps
  inside the card); the Knowledge rail tag chips use the same size and family
  with `max-w-full`, truncating a long tag with its full text kept in the
  button's accessible name and `title`. Put `text-task-meta` on a wrapper when
  the element also sets a text colour, because `cn`/twMerge treats the
  unknown `text-task-meta` as a colour and drops one of them.
- `StatusBadge` routes every task/thread status through `semanticTone`
  (`open`/`in_progress` info, `pending`/`escalated` attention, `completed`
  positive, `failed` danger, the rest neutral) in the body font; there is no
  per-page presentation. The positive tone is Pasture `--accent-text` on
  `--accent-soft`. Entity IDs follow Pasture `.id`: `--color-id-task` and
  `--color-id-thread` alias `--color-info`, and `IdBadge` also renders `job`,
  `schedule` and `run` IDs in `--color-info`, all at weight 500
  (`font-mono text-xs font-medium`, THR-376 PR12).
  Feature vocabularies use the same badge (THR-376 PR9): pass `tone` (a
  `semanticTone` tone), `label` (the feature's own localized word) and
  optionally `dot` (the `.tag` led) and a decorative `icon`. The Todos
  schedule states (`scheduleStatusBadge` in `features/todos/strings.ts`:
  `toneFor` + `statusLabel`, led on armed/firing) and the Skills validation
  states (`skillStatusBadge` in `features/skills/skillStatus.tsx`: Validated
  positive, In catalog neutral, Needs attention / hidden attention,
  permanently removed neutral) render this way; the former `StatusPill` and
  `SkillStatusBadge` are deleted. Words and i18n keys are unchanged; the
  Skills pill moved from mono to the body font.
  Jobs outcome pills (`running` / `exit N` / `failed`) and the Home
  recent-activity verdict pill render through the same tone form (THR-376 r2
  A10), so every tag is 11px / 600 (`.tag`); a tag that must stay a button
  (the Knowledge pending-candidates chip) uses the same `text-overline
  font-semibold` size with its own colours.
  Every `StatusBadge` stays on one line (`whitespace-nowrap`, THR-376 r2
  PR-P0d); a lifecycle `qualifier` truncates with an ellipsis inside the
  badge's `max-w-full` and carries its full text in the `title` attribute.
- `Avatar` (`primitives/Avatar.tsx`, THR-376 PR9) is the one initials
  avatar: Agents roster and detail, Jobs requested-by, Todos owner (list and
  detail), Threads transcript turns, retired agents and the Sidebar founder.
  Props `initials` (rendered verbatim; each caller keeps its existing
  initials rule), `tone` and `size`; always `aria-hidden`. Sizes are the
  review's three steps: `sm` 22px (9.5px label, ds.css `.avatar-sm`), `md`
  30px (11px), `lg` 34px (12px); 700 weight; 50% radius (ds.css Direction A).
  Tones: `founder` is the ds.css `.avatar` gradient (`--accent` → `--info`);
  `manager` / `worker` fill with `--color-agent-manager` / `-worker`, which
  are now aliases of `--color-info` / `--color-accent-default` (the off-palette violet
  and teal literals are gone); `soft` (`--accent-soft` + `--accent-text`) is
  for identities with no role field (Jobs, Todos); `neutral` (`--surface-2` +
  `--ink-2`) is for retired agents. Labels on solid/gradient tones use
  `--color-text-inverse`. ds.css has no other avatar palette. `AgentChip`
  and the roster/detail role dots are 6px role dots, not avatars; they keep
  the `--color-agent-*` tokens.
- `AppBar` renders the Pasture `.topbar` on every page: 56px tall, 26px
  inline padding, a 1px `--border` bottom rule and a translucent `bg-surface-canvas/80`
  background (ds.css `color-mix(in oklab, var(--bg) 80%, transparent)`), with
  the page title in Newsreader 18/500 with -0.2px tracking (ds.css
  `.page-title`, THR-376 PR12). It has no `presentation` prop and no
  page-local `.tasks-appbar` class. Optional `status` (rendered after the
  title) and `actions` (rendered before the language/theme controls) slots
  take a `ReactNode`; pages supply them instead of adding a per-page header.
- Topbar slots (THR-376 r2 PR-S1). `AppShell` wraps the `AppBar` and the routed
  `<main>` in `TopbarSlotsProvider` (`layouts/AppShell/TopbarSlots.tsx`). A
  routed page renders `<TopbarStatus>` (page-status chip(s), placed after the
  title) and `<TopbarActions>` (its primary action, placed before the
  language/theme controls); both portal into the `AppBar`, so handlers and
  dialog state stay with the page and leave with it on unmount. The primary
  action is a `Button size="sm"` (ds.css `.btn-sm`). Status chips use
  `StatusBadge` tones or existing controls; no new tokens. Empty slots are
  `empty:hidden`. Outside the provider (isolated tests/stories) the children
  render inline where written. Current users: New thread, New agent, Add
  custom skill (solid primary), Export (secondary) and New task in
  `TopbarActions`; Health "Live · polling 15s" and the Usage window label +
  Compare switch in `TopbarStatus`.
  Narrow screens (PR-S1 R1): from `lg` (1024px) up, status and actions sit
  inline in the single 56px row; the slot group fills the space between the
  title and the language/theme controls. Below `lg` the `AppBar` wraps: row 1
  keeps the title and the language/theme controls at the 56px height, and the
  slot group moves to a full-width second row (status on the left and allowed
  to wrap, actions pushed right). The row is hidden while every slot is
  empty, so pages without slot content keep the plain 56px bar. Do not add
  per-page breakpoints for slot content; the `AppBar` owns this layout.
  PageTitle exception: Usage renders no visible `PageTitle`. The `AppBar`
  title already names the page and its window label + Compare live in
  `TopbarStatus`, so it keeps a single `sr-only` `<h1>` for assistive tech.
- The `Sidebar` active nav row follows Pasture `[data-dir="a"]
  .nav-item.active`: `bg-accent-soft text-accent-text font-semibold`, with no
  left bar or border. Nav rows use `text-nav` (13.5px) at weight 500, and the
  brand mark is 30px. From `md` up, rows follow ds.css `.nav-item` padding
  8px 10px and gap 11px (`md:py-2 md:px-2.5 md:gap-2.75`, THR-376 PR12);
  below `md` the 56px icon rail keeps its base `py-1.5 px-2 gap-2.5` rows
  unchanged.
- Colour contrast (THR-376 seq9, WCAG AA). `web/design/pasture-ds.css` is
  the visual authority for Direction A light/dark colour values; it is a
  reference copy only and is not imported by the app build. `tokens.css`
  carries its lightness values for the text and accent ramps (chroma/hue
  unchanged). Since THR-376 PR10 the other names in this table are `var()`
  aliases of the semantic token (see "One colour vocabulary" above):

  | Pasture token | tokens.css properties | Light | Dark |
  | --- | --- | --- | --- |
  | `--ink-2` | `text-secondary` (+ light `status-archived`) | `oklch(0.45 0.013 60)` | `oklch(0.76 0.01 75)` |
  | `--ink-3` | `text-muted` (+ dark `status-archived`) | `oklch(0.55 0.012 65)` | `oklch(0.64 0.011 72)` |
  | `--accent` | `accent-default` (`primary`), `agent-founder` | `oklch(0.52 0.11 152)` | `oklch(0.70 0.13 152)` |
  | `--accent-press` | `accent-hover` | `oklch(0.46 0.11 152)` | `oklch(0.76 0.13 152)` |
  | `--positive` | `positive`, `tier-green`, `status-open`, `feedback-success` | `oklch(0.52 0.11 152)` | `oklch(0.72 0.12 152)` |
  | `--info` | `info` (`id-task`, `id-thread`), `feedback-info` | `oklch(0.53 0.10 248)` | `oklch(0.72 0.10 245)` |

  Measured WCAG 2.x ratios: light ink-3 4.52:1 on `--bg` and 4.86:1 on the
  raised surface, ink-2 6.94:1, white on accent/positive 5.23:1, info 4.88:1;
  dark ink-3 5.32:1 on `--bg` and 4.82:1 raised, ink-2 8.34:1, dark
  `text-inverse` on accent 7.40:1 and on positive 7.93:1, info 7.30:1 (dark
  info chroma is ds.css 0.10 since THR-376 PR12; it was an unratified 0.11,
  which had the same 7.30:1). The
  primary Button's hover and press states use the opaque `accent-hover`
  token (Pasture `.btn-primary:hover` → `--accent-press`), not a translucent
  `primary/90`: label on hover/press is 6.57:1 light and 9.19:1 dark. Keep
  new text colours at or above 4.5:1 against the surfaces they sit on.
- Primitives wrap basic interaction and Radix behavior. Patterns combine them
  into reusable product language. Layouts own reusable geometry.
- Components are pure props-in/events-out unless a documented role requires an
  isolation-safe local provider. Reuse them instead of feature-local copies.

## Self-hosted typography

The five `@font-face` rules in `tokens.css` load unmodified full upstream
variable TTFs from `public/fonts/`: Hanken Grotesk normal 400–700,
Newsreader normal 400–600 / italic 400, the Sidebar brand wordmark's
Baloo 2 normal 800 (`/fonts/Baloo2-wght.ttf`), and JetBrains Mono normal
400–600 (`/fonts/JetBrainsMono-wght.ttf`, the official v2.304 release) for
`--font-mono` IDs and code. Family names, weight ranges,
`font-display: swap`, fallbacks and automatic optical sizing stay in the
existing type system. All five faces use local URLs; `web/index.html` loads
no external font stylesheet.

The Hanken Grotesk and Newsreader payload is 1,080,240 bytes before transport
compression, replacing 229,724 bytes of WOFF2 subsets (+850,516 bytes). The
Baloo 2 variable TTF adds 683,200 bytes and the JetBrains Mono variable TTF
303,144 bytes, for 2,066,584 font bytes in total. The full original
`HankenGrotesk-OFL.txt`, `Newsreader-OFL.txt`, `Baloo2-OFL.txt` and
`JetBrainsMono-OFL.txt` add 17,579 bytes alongside the fonts. Vite's existing
public-copy step carries all nine files unchanged into `dist/fonts/`. Immutable upstream revisions, true Git trees, blobs and
notice relations are factual metadata in
[`font-assets.json`](../packaging/notice-data/font-assets.json); that record
does not establish complete emitted or native package attribution.

## Storybook is the catalogue

Storybook supersedes the former committed JSON registry and in-app
`/__design__` route. There is no generated catalogue, registry generator,
route flag, daemon endpoint, MCP transport, or on-disk agent contract.
`npm run dev` and `npm run build` build only the product SPA. Storybook dev
uses the local UI flag profile and `build-storybook` the production profile
(see `ARCHITECTURE.md` § Build-time UI flags).

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
The theme toolbar applies the same `data-theme` contract as the app and defaults
to the light Pasture reference. The `Pasture/*` navigation is the specification
crosswalk. `PASTURE_REFERENCE_REQUIREMENTS` is the single authoritative 25-row
manifest, matching the 25 material sections in the rendered founder HTML. Every
row names a concrete story export and stable rendered semantic selectors.
Timeline and table, avatars/agent rows, and icon tiles are separate requirements.
The manifest also enforces the five page states, review-only
prototype-state selector, 42-entry retired-to-canonical rename map, 55-entry
screen-scoped composition inventory, and narrow-width contract. The existing
`Design System/*` stories remain the
source-to-story identity ledger and detailed component demonstrations. Manager
chrome and canvases use the warm Pasture surface treatment; canvas padding and
examples reflow at 390 CSS px.

## Authoring and enforced coverage

Use CSF `*.stories.tsx`. Each new reusable `.tsx` file in `primitives/`,
`patterns/`, or `layouts/` needs either:

1. an explicit `[source:path#Export] [story:path#Export]` ledger mapping. The
   mapped CSF module's default meta must use the exact runtime source-export
   object as `meta.component`, and the mapped named story must use Storybook's
   default component render (no custom `render` override); or
2. a component-specific technical exclusion below explaining why isolation is
   unsafe/misleading and where the behavior is covered instead.

Use controls/autodocs for real public variants. Interaction stories must be safe
to click/type. Add loading, empty, error, populated, auth, and permission states
only where the reusable unit owns them. Aggregate stories preserve representative
states, controls, and interaction examples; their custom renders do not satisfy
coverage. Historically, `storybook-coverage.test.ts` used Vite's module graph to
import each mapped source and story module and compare runtime object identity.
It also required a mapped named default-render story. The same test rendered every
entry in `PASTURE_REFERENCE_REQUIREMENTS` and queried its declared DOM structure
and semantic attributes. Adversarial fixtures checked that a combined
"Timeline & tables" heading without a timeline and generic implementation
guidance without the prototype selector failed. This narrow contract failed
closed for missing modules/exports/meta, wrong or merely same-named bindings,
metadata-only references, and custom render overrides. It did not claim to
interpret JavaScript or TypeScript execution, and created no generated registry
or runtime catalogue. Reference page states were explicit and fail-closed in the
same test: empty, loading, error/retry, unauthorized, and populated. Navigation
ordering and the 390px manager/canvas responsive contract were independently
asserted. That test was retired under THR-343; web behavior belongs to Product E2E.
Historical assertions do not establish current coverage.
Unauthorized is a visual/copy proposal only because reusable units do not own
auth or permission decisions; no gate is simulated. Loading is a local skeleton
and retry is local state, so Storybook remains daemon-isolated.

## Coverage ledger

**47 reusable components: 45 story-covered, 2 justified exclusions.** Stories
preserve the former catalogue's descriptions, examples, variants, and token
visibility through titles, docs, controls, and representative renders.

| Reusable export | Story or exclusion | Representative states / variants | Tokens |
|---|---|---|---|
| `Avatar` | [source:primitives/Avatar.tsx#Avatar] [story:primitives/Avatar.coverage.stories.tsx#Coverage] Primitives / Avatar Variants | founder/manager/worker/soft/neutral tones; sm 22 / md 30 / lg 34px | `--accent`→`--info` gradient, `--color-agent-*` (aliases of `--color-info`/`--color-accent-default`), `--accent-soft`/`--accent-text`, surface-2, `--color-text-inverse` |
| `Button` | [source:primitives/Button.tsx#Button] [story:primitives/Button.coverage.stories.tsx#Coverage] Primitives / Button States | default/secondary/outline/ghost/destructive/destructive-outline/link; default/sm/lg/icon; loading, disabled, icon, focus, `asChild` | `components.button` |
| `Card` | [source:primitives/Card.tsx#Card] [story:primitives/Card.coverage.stories.tsx#Coverage] Primitives / Card Variants | container (18px), entry (12px); padding default (20) / none; with CardHeader | `--radius-lg`, `--radius-md`, `shadow-pasture-sm`, surface/border |
| `CardHeader` | [source:primitives/Card.tsx#CardHeader] [story:primitives/CardHeader.coverage.stories.tsx#Coverage] Primitives / Card Header | title, meta, actions | `.card-head`, `.card-title` |
| `Dialog` | [source:primitives/Dialog.tsx#Dialog] [story:primitives/Dialog.coverage.stories.tsx#Coverage] Primitives / Dialog Interaction | trigger → portal | `components.dialog` |
| `Drawer` | [source:primitives/Drawer.tsx#Drawer] [story:primitives/Drawer.coverage.stories.tsx#Coverage] Primitives / Drawer Interaction | trigger → drawer | `components.drawer` |
| `DropdownMenu` | [source:primitives/DropdownMenu.tsx#DropdownMenu] [story:primitives/DropdownMenu.coverage.stories.tsx#Coverage] Primitives / Dropdown Menu Interaction | trigger, populated actions | `components.dropdown_menu` |
| `Input` | [source:primitives/Input.tsx#Input] [story:primitives/Input.coverage.stories.tsx#Coverage] Primitives / Input States | empty, populated, disabled | `components.input` |
| `Label` | [source:primitives/Label.tsx#Label] [story:primitives/Label.coverage.stories.tsx#Coverage] Primitives / Label State | associated label | `components.label` |
| `SegmentedControl` | [source:primitives/SegmentedControl.tsx#SegmentedControl] [story:primitives/SegmentedControl.coverage.stories.tsx#Coverage] Primitives / Segmented Control Variants | default/sm; checked/unchecked; disabled option; keyboard focus; Tabs segmented parity | `--radius-pill`, `--accent-soft`/`--accent-text`, surface-2/border |
| `Select` | [source:primitives/Select.tsx#Select] [story:primitives/Select.coverage.stories.tsx#Coverage] Primitives / Select Interaction | selected/open options | `components.select` |
| `SubTabBar` | [source:primitives/SubTabBar.tsx#SubTabBar] [story:primitives/SubTabBar.coverage.stories.tsx#Coverage] Primitives / Sub Tab Bar States | active/inactive navigation | `components.subtabbar` |
| `Tabs` | [source:primitives/Tabs.tsx#Tabs] [story:primitives/Tabs.coverage.stories.tsx#Coverage] Primitives / Tabs Variants | pills, underline, segmented (shares SegmentedControl classes) | `components.tabs` |
| `Textarea` | [source:primitives/Textarea.tsx#Textarea] [story:primitives/Textarea.coverage.stories.tsx#Coverage] Primitives / Textarea States | empty, populated/disabled | `components.textarea` |
| `Tooltip` | [source:primitives/Tooltip.tsx#Tooltip] [story:primitives/Tooltip.coverage.stories.tsx#Coverage] Primitives / Tooltip Interaction | open, hover/focus | `components.tooltip` |
| `AgentChip` | [source:patterns/AgentChip.tsx#AgentChip] [story:patterns/AgentChip.coverage.stories.tsx#Coverage] Patterns / Agent Chip Roles | founder, manager, worker | `components.agent_chip` |
| `AuditRow` | [source:patterns/AuditRow.tsx#AuditRow] [story:patterns/AuditRow.coverage.stories.tsx#Coverage] Patterns / Audit Row Density | complete audit/job fixtures; comfortable, compact, expandable | `components.audit_row` |
| `Composer` | [source:patterns/Composer.tsx#Composer] [story:patterns/Composer.coverage.stories.tsx#Coverage] Patterns / Composer States | ready, abort, error/draft as rendered by the linked story; upload-pending and the lifecycle capabilities (synchronous submit latch, attach/send/remove disabled while pending, one stable identity per file selection so duplicates stay distinct, draft/chip clearing only while the originating destination is still active) are tested in the attachment suites, not story states; 18px local-extension shell | input/button, `--radius-lg` |
| `CrescentMoonBadge` | [source:patterns/CrescentMoonBadge.tsx#CrescentMoonBadge] [story:patterns/CrescentMoonBadge.coverage.stories.tsx#Coverage] Patterns / Crescent Moon Badge State | present | `components.badge` |
| `EmptyState` | [source:patterns/EmptyState.tsx#EmptyState] [story:patterns/EmptyState.coverage.stories.tsx#Coverage] Patterns / Empty State With Action | empty with CTA | `components.empty_state` |
| `FilterSidebar` | [source:patterns/FilterSidebar.tsx#FilterSidebar] [story:patterns/FilterSidebar.coverage.stories.tsx#Coverage] Patterns / Filter Sidebar Interaction | all/selected, counts | `components.filter_sidebar` |
| `FormField` | [source:patterns/FormField.tsx#FormField] [story:patterns/FormField.coverage.stories.tsx#Coverage] Patterns / Form Field States | normal, error | input/label tokens |
| `IdBadge` | [source:patterns/IdBadge.tsx#IdBadge] [story:patterns/IdBadge.coverage.stories.tsx#Coverage] Patterns / Id Badge Kinds | thread, task, job, schedule, run | `components.badge` |
| `InboxRow` | [source:patterns/InboxRow.tsx#InboxRow] [story:patterns/InboxRow.coverage.stories.tsx#Coverage] Patterns / Inbox Row States | default/thread × active/open/archived; default rows retain the 8px bordered interactive shell, while grouped thread rows are flush and their enclosing list owns the border/dividers; nested pills preserved | `components.inbox_row`, `--radius-sm` |
| `KbdChip` | [source:patterns/KbdChip.tsx#KbdChip] [story:patterns/KbdChip.coverage.stories.tsx#Coverage] Patterns / Kbd Chip Combinations | key, chord | `components.kbd_chip` |
| `Markdown` | [source:patterns/Markdown.tsx#Markdown] [story:patterns/Markdown.coverage.stories.tsx#Coverage] Patterns / Markdown Content | heading/list/emphasis/code | typography/code |
| `List` | [source:patterns/List.tsx#List] [story:patterns/List.coverage.stories.tsx#Coverage] Patterns / List Variants | static Row, link RowAction (`asChild`), button RowAction; container/entry; selected row | `.card`, `.divider`, `bg-surface-hover` |
| `MentionAutocomplete` | [source:patterns/MentionAutocomplete.tsx#MentionAutocomplete] [story:patterns/MentionAutocomplete.coverage.stories.tsx#Coverage] Patterns / Mention Autocomplete Populated | populated portal/listbox | surface/border |
| `MentionTextarea` | [source:patterns/MentionTextarea.tsx#MentionTextarea] [story:patterns/MentionTextarea.coverage.stories.tsx#Coverage] Patterns / Mention Textarea Interaction | editable mention | `components.textarea` |
| `Mermaid` | [source:patterns/Mermaid.tsx#default] [story:patterns/Mermaid.coverage.stories.tsx#Coverage] Patterns / Mermaid Diagram | loading → diagram | `components.code_block` |
| `MessageBubble` | [source:patterns/MessageBubble.tsx#MessageBubble] [story:patterns/MessageBubble.coverage.stories.tsx#Coverage] Patterns / Message Bubble Variants | founder, worker, manager, decline, system; non-system shells remain 18px | `components.message_bubble`, `--radius-lg` |
| `PageTitle` | [source:patterns/PageTitle.tsx#PageTitle] [story:patterns/PageTitle.coverage.stories.tsx#Coverage] Patterns / Page Title Slots | title only; eyebrow; lede; eyebrow + lede + actions; actions | `--text-page-title`, `--text-eyebrow`, body |
| `RecipientsInput` | [source:patterns/RecipientsInput.tsx#RecipientsInput] [story:patterns/RecipientsInput.coverage.stories.tsx#Coverage] Patterns / Recipients Input Interaction | editable prefix | mention/surface |
| `Sparkline` | [source:patterns/Sparkline.tsx#Sparkline] [story:patterns/Sparkline.coverage.stories.tsx#Coverage] Patterns / Sparkline Variants | default/green/yellow/red | semantic tiers |
| `StatValue` | [source:patterns/StatValue.tsx#StatValue] [story:patterns/StatValue.coverage.stories.tsx#Coverage] Patterns / Stat Value Formats | token/count, right/inline | `components.stat_value` |
| `StatusBadge` | [source:patterns/StatusBadge.tsx#StatusBadge] [story:patterns/StatusBadge.coverage.stories.tsx#Coverage] Patterns / Status Badge States | all lifecycle states; feature-vocabulary tone + label (+ led dot, icon) | `components.badge` |
| `TaskCard` | [source:patterns/TaskCard.tsx#TaskCard] [story:patterns/TaskCard.coverage.stories.tsx#Coverage] Patterns / Task Card Density | populated/active; comfortable, compact; injected routes | badge/card |
| `ThreadHeader` | [source:patterns/ThreadHeader.tsx#ThreadHeader] [story:patterns/ThreadHeader.coverage.stories.tsx#Coverage] Patterns / Thread Header States | open/dream/action, archived | thread layout |
| `TraceTree` | [source:patterns/TraceTree.tsx#TraceTree] [story:patterns/TraceTree.coverage.stories.tsx#Coverage] Patterns / Trace Tree Density | recursive cost fixture; comfortable, compact | `components.trace_tree` |
| `TypingBubble` | [source:patterns/TypingBubble.tsx#TypingBubble] [story:patterns/TypingBubble.coverage.stories.tsx#Coverage] Patterns / Typing Bubble States | working, queued; shell remains 18px | info/muted, `--radius-lg` |
| `AppBar` | [excluded:AppBar] Reads live shell/org/navigation contexts and hosts product commands; AppShell/route tests cover it. | shell context in tests | topbar/grid |
| `ErrorBoundary` | [excluded:ErrorBoundary] Lifecycle capture/reset is not a static catalogue unit; component and route tests cover error/recovery. | normal/error/reset in tests | feedback |
| `Sidebar` | [source:layouts/AppShell/Sidebar.tsx#Sidebar] [story:layouts/AppShell/Sidebar.coverage.stories.tsx#Coverage] Layouts / Sidebar Branches | disabled navigation and footer account; focused enabled-navigation story | sidebar/grid |
| `ContentWrap` | [source:layouts/ContentWrap/ContentWrap.tsx#ContentWrap] [story:layouts/ContentWrap/ContentWrap.coverage.stories.tsx#Coverage] Layouts / Content Wrap Responsive | bounded responsive content | layout content/wrap |
| `DashboardLayout` | [source:layouts/DashboardLayout.tsx#DashboardLayout] [story:layouts/DashboardLayout.coverage.stories.tsx#Coverage] Layouts / Dashboard Layout Populated | four populated slots | layout grid |
| `ThreadsLayout` | [source:layouts/ThreadsLayout.tsx#ThreadsLayout] [story:layouts/ThreadsLayout.coverage.stories.tsx#Coverage] Layouts / Threads Layout Populated | inbox/detail columns | threads grid |

## Frontend readiness matrix

| State | Applicability / evidence |
|---|---|
| Interaction | Applicable: portal/menu/select/tabs/filter/composer/mention stories are safe locally. |
| Loading | Applicable only to Mermaid's local render transition; backend loading is N/A to pure units. |
| Empty | Applicable to EmptyState and blank input examples. |
| Error | Applicable to FormField validation and Composer draft-preserving errors (send, upload and mapped artifact error codes). |
| Populated | Applicable across all three layers. |
| Auth | N/A: reusable units do not own authentication; context consumers are excluded and app-tested. |
| Permission | N/A: feature/shell owners authorize before passing props; context consumers are excluded. |

## Deterministic verification and acceptance

`scripts/verify-design-system.sh` runs typecheck, lint, unit coverage, the static
Storybook build, full-tree raw-colour enforcement, the unknown-token scan and
the Pasture parity check.

### Pasture parity check (THR-376 PR12)

`bash scripts/verify-design-system.sh --scan-pasture` (exit 7 on drift) pins
`tokens.css` and the owning components to `web/design/pasture-ds.css`
Direction A, in light and dark. For each surface it resolves the ds.css value
(following `var()` and the `[data-dir="a"]` overrides) and what the code
produces: the token value in `@theme` (light) or `:root[data-theme="dark"]`
(dark), with `var()` aliases followed, reached through the component's own
class list. It pins:

| Surface | Owner | Pinned against ds.css |
| --- | --- | --- |
| Topbar | `AppBar` root | `.topbar` height, padding, gap, 80% `--bg` background, 1px `--border` bottom rule |
| Page title | `PageTitle` `<h1>`; `AppBar` title | `.h1` and `.page-title` family, size, weight, letter-spacing |
| Card | `Card` base + `container` + default padding | `.card` surface, 1px `--border`, `--radius-lg`, `--shadow-sm`; `.card-pad` padding |
| Nav row / active | `SidebarNavItem` (enabled and disabled rows) | `.nav-item` padding and gap; `.nav-item.active` fill, text colour, weight 600, no shadow, no left bar |
| ID | `IdBadge` (every `kind`) | `.id` colour (`--info`, both themes), mono family, 12px, weight 500 |

Only unconditional ds.css rules count: `@media` blocks (the under-860px
topbar) and Direction B are ignored. Component classes are read at the
desktop rail: an `md:` utility overrides the unprefixed one; state variants
(`hover:`, `focus-visible:`) are ignored. There is no allowlist or exemption.
A source the check cannot locate is drift, not a pass.

When ds.css changes, the check goes red on the affected surface: change the
token in `tokens.css` (both themes where they differ) or the owning component's
class list until it agrees, and update the tables above. Do not edit ds.css
to match the code; it is the founder-attached authority. If an owner is
renamed or restructured, update the extraction pattern in `scan_pasture` and
add the matching case to `scripts/test-design-system-pasture-parity.sh`. That
suite runs the check on a trimmed fixture
(`scripts/fixtures/design-system-pasture-parity/`) and mutates one token,
class or reference value per case. `scripts/verify-design-system-colour-gate.sh`
runs the suite and the real-tree scan inside the existing Web Lint
"Design-system colour gate" step. `scripts/local_ci.sh web|all` and the
GitHub Web gate explicitly run SPA and Storybook builds once each. Storybook is
not a product prebuild hook, preventing duplicate builds.

- [ ] Stable imports and shipped component behavior remain unchanged.
- [ ] No `/__design__`, route flag, registry, generator, freshness hook, or stale metadata remains.
- [ ] Every reusable unit has meaningful discoverable coverage or a justified exclusion.
- [ ] Autodocs/controls, semantic tokens, themes, and safe local providers work.
- [ ] Every rendered Pasture reference section and its responsive 390 px presentation is represented by a discoverable `Pasture/*` story.
- [ ] No live daemon, hosted visual service, or product behavior is introduced.
- [ ] Lint, typecheck, unit tests, SPA build, Storybook build, design-system verification, browser evidence, and Node 24 local CI pass.

System Assistant is retired (THR-294): no dock, settings, API domain or Cmd/Ctrl-K
binding. Shared Composer and TypingBubble remain for ordinary threads.
