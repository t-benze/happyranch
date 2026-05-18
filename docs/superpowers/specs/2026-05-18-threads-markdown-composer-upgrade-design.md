# Threads UI — Markdown rendering & composer upgrade

**Date:** 2026-05-18
**Owner:** founder
**Companion to:** `docs/superpowers/specs/2026-05-14-web-ui-design.md` (web-UI architecture), `web/DESIGN_SYSTEM.md` (design-system contract), `web/UI_SPEC.md` (per-screen UX).

## 1. Problem

Opening a thread whose body contains fenced code blocks (e.g. `THR-003`, seq 1 = 11.6 KB markdown with ~10 ` ``` ` blocks) makes the threads sidebar disappear off-screen.

Root cause, observed live via `playwright-cli` against the running daemon:

1. `MessageBubble.tsx` wraps the markdown body in `<div className="prose prose-invert prose-sm">`, but `@tailwindcss/typography` is not installed, so the `prose` classes are inert.
2. Without the typography plugin's `overflow-x: auto` rescue on `pre`, `<pre>` keeps its default `white-space: pre` and grows to the widest unbreakable line (~2794 px for the THR-003 body).
3. `ThreadsLayout` uses `grid-cols-[320px_1fr]`. In CSS Grid, `1fr` resolves to `minmax(auto, 1fr)`, so the detail column honors min-content and expands to 2860 px → total grid width 3180 px on a 1440 px viewport.
4. `<main className="flex-1 overflow-hidden">` clips horizontally so no scrollbar appears.
5. `MessageTranscript` calls `endRef.current.scrollIntoView({block:'end'})` on every message change. `scrollIntoView` scrolls all ancestor containers — including `overflow:hidden` ones — via `scrollLeft`. `main.scrollLeft` jumps to 336 px.
6. The 320 px `<aside>` sits at `x=0` inside the grid → appears at viewport `x = -336`, fully off-screen.

Verification (Playwright eval against THR-003 at 1440×900):

```
main.scrollLeft      = 336
aside.getBoundingClientRect().x = -336
grid.scrollWidth     = 3180  (320 + 2860)
main.clientWidth     = 1440
widest <pre>         = 2794  (white-space: pre, overflow-x: visible)
parent prose styles  = inert (typography plugin not installed)
```

Beyond the bug, the founder also wants markdown to **actually render** (code blocks, tables, blockquotes, diagrams) and the composer to grow up to match (`@`-mention agents, `Cmd/Ctrl+Enter` to send, draft persistence).

## 2. Goals & non-goals

### Goals

- Sidebar stays visible (clientWidth 320, left edge at viewport x=0) on every thread regardless of body content.
- Markdown bodies render with proper styling: headings, lists, blockquotes, inline code, links, **bold**, *italic*, GFM tables, syntax-highlighted fenced code blocks (with plain monospace fallback for unknown languages), and Mermaid diagrams.
- Composer supports: `Cmd/Ctrl+Enter` sends, `Enter` inserts newline; `@` triggers an agent-autocomplete popup; auto-grow textarea up to ~10 lines then scroll internally; drafts persist per-thread in `localStorage`.
- Wire protocol unchanged. No daemon route changes. No agent prompt changes. No SQLite migration.

### Non-goals

- No persistent channel/DM sidebar (the current inbox column stays).
- No chat-stream / Slack-bubble visual restructure. `MessageBubble`'s outer shell (bordered article with header line) stays byte-identical.
- No formatting toolbar / preview tab. Authoring stays plain markdown in a textarea.
- No agent-emits-HTML pivot. Agents keep emitting markdown.
- No light-mode work. Dark mode only for v1; light-mode highlighting deferred until the app actually renders light mode.

## 3. Architecture

### 3.1 New files (4)

| Path | Layer | Purpose |
|---|---|---|
| `web/src/design-system/patterns/Markdown.tsx` | pattern | Pure component `<Markdown body={string} />`. Owns the react-markdown plugin chain, syntax highlighting, and Mermaid dispatch. |
| `web/src/design-system/patterns/Mermaid.tsx` | pattern | Tiny wrapper around `mermaid.render`. Imported only via dynamic `import()` from `Markdown.tsx` (so the library is lazy). See §4.3. |
| `web/src/design-system/patterns/MentionAutocomplete.tsx` | pattern | Pure floating popup `<MentionAutocomplete anchor={Rect} query={string} agents={AgentSummary[]} onSelect onDismiss />`. No fetching — caller supplies the agents. |
| `web/src/design-system/patterns/MarkdownTokens.css` | pattern (style) | All `.gl-prose` typography rules. Imported once from `web/src/styles.css`. |

Provider plumbing for the new `agents` domain (extends the existing `DataContext`):

- `web/src/design-system/providers/DataContext.ts` (edit) — add an `AgentsApi` interface + an `agents: AgentsApi` field on `DataContextValue`.
- `web/src/design-system/providers/_real-agents.ts` (new) — real `AgentsApi` over `listAgents(slug)` from `lib/api/agents.ts`; `staleTime: 5 * 60 * 1000`. The active slug comes from the provider context, not from the caller.
- `web/src/design-system/providers/_mock-agents.ts` (new) — mock `AgentsApi` returning a canned list so prototypes / unit tests don't hit the network.
- `web/src/design-system/providers/AppProvider.tsx` (edit) and `PrototypeProvider.tsx` (edit) — install the real / mock implementations respectively.
- `web/src/hooks/agents.ts` (new) — public, provider-aware: `export const useAgentsList = () => useData().agents.useAgentsList()`. Compositions call this; they never reach into the provider directly.

Patterns rather than feature-local files because Markdown rendering will be reused later by Talks / KB / Audit. MentionAutocomplete is similarly reusable.

### 3.2 Edited files (3)

| Path | Change |
|---|---|
| `web/src/design-system/patterns/MessageBubble.tsx` | Replace the inert `<div className="prose prose-invert prose-sm">` with `<Markdown body={body ?? ''} />`. Article shell, variant border, header line — all unchanged. |
| `web/src/design-system/patterns/Composer.tsx` | Add autogrow, mention trigger, draft persistence. Existing Cmd/Ctrl+Enter handler is preserved (current code already sends on `Enter` with `ctrlKey \|\| metaKey`). New required props: `agents: AgentSummary[]`, `threadId: string`. `onSend` signature gains `addressedTo: string[]`. |
| `web/src/design-system/layouts/ThreadsLayout.tsx` (line 26) | `grid-cols-[320px_1fr]` → `grid-cols-[320px_minmax(0,1fr)]`. Independent fix that prevents the detail column from growing past `viewport − 320` regardless of body content. |

### 3.3 Layer compliance (per `web/ARCHITECTURE.md`)

- Patterns may import primitives + `@/lib/utils` only. `Markdown` imports nothing from `lib/api`, `hooks`, or features. `MentionAutocomplete` is pure props in / events out.
- The only feature-level change is in `ThreadsPage.tsx`: it threads `agents` (from a new `useAgentsList` hook) and `threadId` into `<Composer>`, and forwards `addressedTo` from `onSendFollowUp` into the existing `useSendFollowUp` mutation (which already accepts `addressed_to`).

## 4. Markdown rendering pipeline

### 4.1 Dependencies

| Package | Status | Size (gz) |
|---|---|---|
| `react-markdown` | already in deps, keep | ~30 KB |
| `remark-gfm` | already in deps, keep | ~20 KB |
| `rehype-highlight` | **new** | ~15 KB |
| `highlight.js/styles/github-dark-dimmed.css` | **new** (CSS only) | ~8 KB |
| `mermaid` | **new**, lazy via dynamic import | ~150 KB (only paid when a `mermaid` block appears) |

`rehype-sanitize` is intentionally not added. react-markdown does not pass through raw HTML by default (we do **not** enable `rehypeRaw`). Agent-emitted HTML in a message body lands as a literal string inside `<p>` or `<pre>`. If a future change enables `rehypeRaw`, sanitization must be added in the same PR.

### 4.2 Component sketch

```tsx
// web/src/design-system/patterns/Markdown.tsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Suspense, lazy, type ComponentProps } from 'react';

const Mermaid = lazy(() => import('./Mermaid'));  // Mermaid is a sibling pattern file

function CodeOrMermaid(props: ComponentProps<'code'>) {
  const lang = /language-(\w+)/.exec(props.className ?? '')?.[1];
  if (lang === 'mermaid' && typeof props.children === 'string') {
    return (
      <Suspense fallback={<pre className="gl-prose-mermaid-loading">Rendering diagram…</pre>}>
        <Mermaid source={props.children} />
      </Suspense>
    );
  }
  return <code {...props} />;
}

export function Markdown({ body }: { body: string }) {
  return (
    <div className="gl-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { ignoreMissing: true, detect: true }]]}
        components={{ code: CodeOrMermaid }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}
```

`rehype-highlight`'s `detect: true` infers the language for unannotated fences using `highlight.js/lib/common` (15+ common languages). Unknown languages render as plain `<pre><code>` with no highlight classes — no error.

### 4.3 Mermaid

`Mermaid.tsx` is a tiny wrapper:

```tsx
import mermaid from 'mermaid';
mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' });

export default function Mermaid({ source }: { source: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [errored, setErrored] = useState(false);
  const id = useId().replace(/:/g, '_');
  useEffect(() => {
    mermaid.render(`m_${id}`, source).then(
      r => setSvg(r.svg),
      () => setErrored(true),
    );
  }, [source, id]);
  if (errored) return <pre className="gl-prose-mermaid-failed">{source}</pre>;
  return <div className="gl-prose-mermaid" dangerouslySetInnerHTML={{ __html: svg ?? '' }} />;
}
```

`securityLevel: 'strict'` prevents user `<script>` content inside Mermaid sources from executing. The error fallback renders the raw source as a plain `<pre>` so a malformed diagram never blanks the message.

### 4.4 `.gl-prose` CSS

Inline rules (no Tailwind plugin). All selectors scoped under `.gl-prose`, using existing semantic tokens — no new tokens:

```css
.gl-prose { color: var(--color-text-primary); font-size: 14px; line-height: 1.55; }
.gl-prose p { margin: 0.5em 0; }
.gl-prose h1, .gl-prose h2, .gl-prose h3, .gl-prose h4 { font-weight: 600; margin: 0.8em 0 0.4em; line-height: 1.25; }
.gl-prose h1 { font-size: 1.4em; }
.gl-prose h2 { font-size: 1.2em; }
.gl-prose h3 { font-size: 1.05em; }
.gl-prose h4 { font-size: 1em; color: var(--color-text-secondary); }
.gl-prose a { color: var(--color-accent-default); text-decoration: underline; }
.gl-prose blockquote { border-left: 3px solid var(--color-border-default); padding-left: 0.8em; color: var(--color-text-secondary); margin: 0.6em 0; }
.gl-prose ul, .gl-prose ol { padding-left: 1.4em; margin: 0.4em 0; }
.gl-prose li { margin: 0.15em 0; }
.gl-prose code:not(pre code) { font-family: var(--font-mono); font-size: 0.92em; padding: 0.1em 0.35em; background: var(--color-surface-raised); border-radius: 3px; }
.gl-prose pre { background: var(--color-surface-raised); border: 1px solid var(--color-border-subtle); border-radius: 6px; padding: 0.8em 1em; overflow-x: auto; max-width: 100%; font-size: 0.85em; line-height: 1.45; margin: 0.6em 0; }
.gl-prose pre code { background: transparent; padding: 0; font-family: var(--font-mono); }
.gl-prose table { display: block; overflow-x: auto; max-width: 100%; border-collapse: collapse; margin: 0.6em 0; }
.gl-prose th, .gl-prose td { border: 1px solid var(--color-border-default); padding: 0.4em 0.7em; text-align: left; }
.gl-prose th { background: var(--color-surface-sunken); font-weight: 600; }
.gl-prose img { max-width: 100%; height: auto; }
.gl-prose .gl-prose-mermaid { background: var(--color-surface-raised); border: 1px solid var(--color-border-subtle); border-radius: 6px; padding: 0.8em; overflow-x: auto; }
.gl-prose .gl-prose-mermaid-loading,
.gl-prose .gl-prose-mermaid-failed { color: var(--color-text-muted); font-style: italic; }
```

The load-bearing rules for the layout bug are `pre { overflow-x: auto; max-width: 100% }` and `table { display: block; overflow-x: auto; max-width: 100% }`. Either alone keeps wide content from expanding the parent column; combined with `minmax(0, 1fr)` in §3.2, the sidebar is double-protected.

The single new highlight.js theme CSS is imported once from `web/src/styles.css`:

```css
@import 'highlight.js/styles/github-dark-dimmed.css';
```

## 5. Composer v2

### 5.1 Key handling

```
| Cmd/Ctrl + Enter           → send (if not pending & not disabled & body non-empty)
| Enter                      → insert newline
| Shift + Enter              → insert newline (parity)
| Esc (when popup open)      → dismiss popup
| ArrowUp / ArrowDown (popup) → move selection
| Enter / Tab (when popup)   → insert selected mention
```

### 5.2 Mention model

1. After each keystroke, scan from `selectionStart` backward for `@` until whitespace. If matched, `query` = chars after `@`.
2. Open `<MentionAutocomplete>` anchored to the textarea's caret rect (via `getBoundingClientRect()` + a hidden mirror div for caret positioning — well-known trick).
3. Agents are passed in via the `agents` prop. `ThreadsPage` calls the provider-aware `useAgentsList()` (which reads the active slug from `DataContext`) and forwards the result. Composer never reads org context itself, keeping the pattern reusable. Query staleTime: `5 * 60 * 1000` (5 min).
4. Selecting an agent replaces the `@<query>` token with `@<agent.name> ` (trailing space).
5. On send: regex-scan the final body for `@([\w-]+)` tokens (case-sensitive). For each token, resolve against the agents list (exact match by `name`). Special token: a literal lowercase `@all` always resolves to `'@all'` regardless of agent presence (matches the existing CLI convention). The resulting deduped set becomes `addressed_to`. If the set is empty (no mentions in the body), `addressed_to = ['@all']` — preserves the current default.

### 5.3 Autogrow

`useAutoGrow(ref)` hook: on `input` event resets `style.height = 'auto'`, then `style.height = Math.min(scrollHeight, MAX_PX) + 'px'`. `MAX_PX = 240` (≈10 lines at the chosen line-height). Past that, the textarea scrolls internally.

### 5.4 Draft persistence

- Storage key: `grassland:draft:<org_slug>:<thread_id>`.
- Hook: `useThreadDraft(threadId)` returns `{ draft, setDraft, clearDraft }`. Reads on mount, writes debounced 300 ms.
- On successful `onSend`: `clearDraft()`. On failed send: draft stays.
- Per-draft cap: 65,536 characters (`draft.length`). Larger drafts skip persistence silently (one `console.debug`, no UI). No cross-tab sync.

### 5.5 Updated props

```ts
interface ComposerProps {
  // EXISTING — preserved verbatim
  disabled?: boolean;
  pending?: boolean;
  errorMessage?: string | null;
  helper?: string;
  placeholder?: string;
  registerFocus?: (focus: () => void) => void;

  // CHANGED — second arg added
  onSend: (markdown: string, addressedTo: string[]) => unknown | Promise<unknown>;

  // NEW (required)
  agents: AgentSummary[];        // from useAgentsList(orgSlug)
  threadId: string;
}
```

`AgentSummary` is imported from `web/src/lib/api/agents.ts`. `ThreadsPage.tsx` is the only caller — adding both required props is a typed compile-time error that surfaces the wiring. The `onSend` signature change is backwards-compatible at runtime (extra arg is ignored by old callbacks) but the TS type tightens to force callers to forward `addressedTo`.

## 6. Layout fix

Single-line change in `web/src/design-system/layouts/ThreadsLayout.tsx:26`:

```diff
-    <div className="grid h-full grid-cols-[320px_1fr] grid-rows-[minmax(0,1fr)]">
+    <div className="grid h-full grid-cols-[320px_minmax(0,1fr)] grid-rows-[minmax(0,1fr)]">
```

`minmax(0, 1fr)` forces the second column's min track size to 0, so wide content overflows inside the column rather than expanding it. Combined with `pre/table { overflow-x: auto }`, both layers protect against unbreakable content.

## 7. Tests

### 7.1 Unit (Vitest + RTL)

- `web/src/design-system/patterns/Markdown.test.tsx` (new)
  - Plain paragraph renders text content.
  - Fenced ` ```ts ` block renders `<pre>` with at least one `hljs-*` class on a descendant.
  - Fenced block with unknown language `xyz` renders `<pre>` with no error.
  - GFM table fixture renders `<thead>` + `<tbody>`.
  - Mermaid block: mocks `mermaid.render` to resolve `{ svg: '<svg/>' }`; asserts the rendered HTML contains an `<svg`.
  - Mermaid block: mocks `mermaid.render` to reject; asserts the failure fallback shows raw source.

- `web/src/design-system/patterns/MentionAutocomplete.test.tsx` (new)
  - ArrowUp/Down moves selection.
  - Esc fires `onDismiss`.
  - Enter fires `onSelect` with the active agent.
  - Filtering by query prefix.

- `web/src/design-system/patterns/Composer.test.tsx` (extend existing if present, else new)
  - Cmd+Enter (and Ctrl+Enter) calls `onSend`. Enter alone does not.
  - Typing `@de` opens autocomplete with matching agents.
  - Selecting an agent inserts `@<name> ` and `onSend` receives `addressedTo: ['<name>']`.
  - Send with no mentions ⇒ `addressedTo: ['@all']`.
  - Draft restored from `localStorage` on remount.
  - Disabled state suppresses mention popup.

### 7.2 Integration (`web/src/features/threads/ThreadsPage.test.tsx`)

Add one case: mock a thread whose first message body contains a fenced code block. Render `<ThreadsPage>`. Assert the rendered DOM contains a `<pre>` and `aside.clientWidth === 320` after layout. This case is the layout-regression sentinel for the THR-003 bug class.

### 7.3 Manual verification (per `superpowers:verification-before-completion`)

Before claiming done, run against the live daemon:

1. `scripts/build_web.sh` + open `http://localhost:<daemon-port>/orgs/tourism-org/threads/THR-003` at 1440×900 viewport via `playwright-cli`.
2. `eval` to confirm: `main.scrollLeft === 0`, `aside.clientWidth === 320`, `aside.getBoundingClientRect().x === 0`.
3. Confirm a fenced code block inside the bubble scrolls horizontally without scrolling the page.
4. Open a test thread with a `mermaid` block; confirm SVG renders.
5. In the composer: type a sentence then Cmd+Enter → message sends. Type `@` → autocomplete shows org agents. Reload page → unsent draft is restored.

## 8. Rollout

Single PR. Estimated diff ~600–800 LOC including tests; ~350 LOC net new TS code + ~80 LOC CSS + ~250 LOC tests.

Bundle delta on the initial chunk: ~+45 KB gz (`rehype-highlight` + `highlight.js` runtime + theme CSS + react-markdown plugin glue). Mermaid (~150 KB gz) is lazy-imported on first encounter.

No daemon, schema, or agent-prompt changes. No migration. The PR is independently revertible.

## 9. Acceptance criteria

The design ships when all of the following hold against the running daemon:

1. **Sidebar visible on THR-003** — at 1440×900 viewport, `aside.clientWidth === 320` and `aside.getBoundingClientRect().x === 0` after loading `/orgs/tourism-org/threads/THR-003`.
2. **Code blocks render and scroll** — seq-1's fenced ` ```markdown ` block in THR-003 renders with `<pre>` having `overflow-x: auto`, and horizontally scrolling that `<pre>` does not move the page or the aside.
3. **Mermaid renders** — a test message body containing ` ```mermaid\nflowchart LR; A-->B\n``` ` produces an `<svg>` inside the bubble.
4. **Mentions wire to addressed_to** — typing `@<agent_name>` in the composer and sending results in `addressed_to` on the new message equal to `["<agent_name>"]`. Sending with no mention sets `addressed_to = ["@all"]`.
5. **Send key + draft** — Cmd/Ctrl+Enter sends and clears the draft; Enter inserts a newline; navigating away and back restores an unsent draft.
6. **No regressions** — `npm run test` and `uv run pytest tests/contract/` both pass. The OpenAPI snapshot is unchanged (no daemon changes).

## 10. Out-of-scope (explicit)

- Channel/DM sidebar, chat-stream bubble layout, formatting toolbar, preview tab.
- Agents emitting HTML.
- Light-mode highlighting theme.
- Slash-commands, message reactions, message editing, threading-within-a-thread.
- Cross-tab draft sync.
- Server-side markdown rendering (we keep markdown stored verbatim in `body_markdown`).
