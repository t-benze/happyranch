# Threads UI — Markdown rendering & composer upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make THR-003 readable (markdown renders properly, sidebar stays put) and bring the composer up to operator-friendly behavior (`@`-mentions, autogrow, drafts).

**Architecture:** In-place upgrade of `MessageBubble` and `Composer` in `web/src/design-system/patterns/`. New `Markdown` + `Mermaid` + `MentionAutocomplete` patterns. New `agents` domain on `DataContext` (real + mock implementations). Three small editor hooks (`useAutoGrow`, `useThreadDraft`, `useAgentsList`). One-line grid fix in `ThreadsLayout`.

**Tech Stack:** React 18 + TypeScript strict + Tailwind v4 + TanStack Query v5 + Vitest + react-markdown v9 + remark-gfm + rehype-highlight + highlight.js + mermaid (lazy).

**Spec:** `docs/superpowers/specs/2026-05-18-threads-markdown-composer-upgrade-design.md`. The plan assumes the spec; it does not restate scope. Anywhere a step references a long CSS block, code sketch, or behavior list that is documented verbatim in the spec, the step says "see spec §X.Y" — copy from there, do not paraphrase.

**Working directory for every step:** `web/` (commands shown rooted there). Git commands run from the repo root (`/Users/tangbz/projects/my-opc`).

---

## File map (locked at planning time)

**New files (8):**

- `web/src/design-system/patterns/Markdown.tsx`
- `web/src/design-system/patterns/Markdown.test.tsx`
- `web/src/design-system/patterns/Mermaid.tsx`
- `web/src/design-system/patterns/MentionAutocomplete.tsx`
- `web/src/design-system/patterns/MentionAutocomplete.test.tsx`
- `web/src/design-system/patterns/MarkdownTokens.css`
- `web/src/design-system/patterns/Composer.test.tsx`
- `web/src/design-system/providers/_real-agents.ts`
- `web/src/design-system/providers/_mock-agents.ts`
- `web/src/hooks/agents.ts`

**Edited files (8):**

- `web/package.json` — three new deps.
- `web/src/styles.css` — two `@import` lines.
- `web/src/design-system/layouts/ThreadsLayout.tsx` — one-class change on line 26.
- `web/src/design-system/patterns/MessageBubble.tsx` — swap prose div for `<Markdown>`.
- `web/src/design-system/patterns/Composer.tsx` — autogrow, draft persistence, mention trigger, new props.
- `web/src/design-system/providers/DataContext.ts` — add `AgentsApi`.
- `web/src/design-system/providers/AppProvider.tsx` — wire `realAgentsApi`.
- `web/src/design-system/providers/PrototypeProvider.tsx` — wire `mockAgentsApi`.
- `web/src/features/threads/ThreadsPage.tsx` — thread `agents` and `threadId` into `<Composer>`, forward `addressedTo` into `useSendFollowUp`.
- `web/src/mocks/agents.ts` — populate `MOCK_AGENTS` with two AgentSummary entries so the prototype's mention popup has content.

---

## Task 1: Layout fix (the sidebar-collapse one-liner)

Independent of everything else; shipped first so the user-visible bug is gone even if subsequent tasks are paused.

**Files:**
- Modify: `web/src/design-system/layouts/ThreadsLayout.tsx:26`

- [ ] **Step 1.1: Read the current line**

Run: `grep -n grid-cols web/src/design-system/layouts/ThreadsLayout.tsx`
Expected: line 26 contains `grid-cols-[320px_1fr]`.

- [ ] **Step 1.2: Apply the fix**

Edit `web/src/design-system/layouts/ThreadsLayout.tsx` line 26:

```diff
-    <div className="grid h-full grid-cols-[320px_1fr] grid-rows-[minmax(0,1fr)]">
+    <div className="grid h-full grid-cols-[320px_minmax(0,1fr)] grid-rows-[minmax(0,1fr)]">
```

- [ ] **Step 1.3: Type-check + build still pass**

Run from `web/`:

```bash
npm run typecheck
npm run build
```

Expected: both succeed. (The `build` runs `build:registry` which scans `meta` blocks — none changed, so registry is unaffected.)

- [ ] **Step 1.4: Commit**

```bash
git add web/src/design-system/layouts/ThreadsLayout.tsx
git commit -m "fix(web): widen ThreadsLayout 1fr to minmax(0,1fr)

Prevents detail-pane content with unbreakable min-content (wide <pre>,
wide <table>) from expanding the second grid track past viewport-320,
which combined with MessageTranscript's scrollIntoView() pushes the
320px aside off-screen. Independently revertable fix for the
THR-003 sidebar-collapse class of bugs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Install markdown rendering dependencies

**Files:**
- Modify: `web/package.json` and `web/package-lock.json` (via `npm install`)

- [ ] **Step 2.1: Install runtime deps**

Run from `web/`:

```bash
npm install --save rehype-highlight@^7.0.2 highlight.js@^11.10.0 mermaid@^11.4.0
```

Expected: three packages added to `dependencies`. No peer-dep warnings beyond what already prints.

- [ ] **Step 2.2: Verify the build still works**

Run from `web/`:

```bash
npm run build
```

Expected: build passes; bundle size increases (the `npm run build` output prints chunk sizes). Note the new total for later comparison.

- [ ] **Step 2.3: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(web): add rehype-highlight, highlight.js, mermaid

Direct dependencies for the markdown rendering upgrade. mermaid will
be lazy-imported from Markdown.tsx so it stays out of the initial
bundle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: MarkdownTokens.css + wire it into styles.css

**Files:**
- Create: `web/src/design-system/patterns/MarkdownTokens.css`
- Modify: `web/src/styles.css`

- [ ] **Step 3.1: Create `MarkdownTokens.css`**

Create `web/src/design-system/patterns/MarkdownTokens.css` with **the exact CSS block from the spec §4.4** (verbatim — do not paraphrase, do not collapse rules). The whole `.gl-prose ...` block, including the `.gl-prose-mermaid` rules.

- [ ] **Step 3.2: Wire it into `styles.css`**

Edit `web/src/styles.css`. After the existing `@import "./design-system/tokens/tokens.css";` line, add two imports:

```css
@import "./design-system/patterns/MarkdownTokens.css";
@import "highlight.js/styles/github-dark-dimmed.css";
```

- [ ] **Step 3.3: Verify build**

Run from `web/`:

```bash
npm run build
```

Expected: builds; the new CSS is bundled into the existing `dist/assets/index-*.css` (CSS size grows ~3 KB before minification).

- [ ] **Step 3.4: Commit**

```bash
git add web/src/design-system/patterns/MarkdownTokens.css web/src/styles.css
git commit -m "feat(web): add .gl-prose typography rules + highlight.js theme

Standalone CSS block scoped under .gl-prose (no Tailwind typography
plugin). The load-bearing rules are 'pre { overflow-x: auto }' and
'table { display: block; overflow-x: auto }' — defense in depth for
the wide-content layout class of bugs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Markdown pattern (without Mermaid yet)

Test-first. We build the basic markdown renderer and prove tables, code blocks, and unknown languages all work. Mermaid arrives in Task 5.

**Files:**
- Create: `web/src/design-system/patterns/Markdown.tsx`
- Create: `web/src/design-system/patterns/Markdown.test.tsx`

- [ ] **Step 4.1: Write the failing test**

Create `web/src/design-system/patterns/Markdown.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Markdown } from './Markdown';

describe('Markdown', () => {
  it('renders a plain paragraph', () => {
    render(<Markdown body="Hello world" />);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  it('renders fenced code with language as <pre><code class="language-ts ...">', () => {
    render(<Markdown body={'```ts\nconst x = 1;\n```'} />);
    const code = document.querySelector('pre code');
    expect(code).not.toBeNull();
    expect(code!.className).toMatch(/language-ts/);
    // rehype-highlight adds hljs classes; assert it ran at least once.
    expect(code!.className).toMatch(/hljs/);
  });

  it('renders fenced code with an unknown language without throwing', () => {
    render(<Markdown body={'```xyz\nfoo\n```'} />);
    expect(document.querySelector('pre code')).not.toBeNull();
  });

  it('renders a GFM table with thead and tbody', () => {
    render(<Markdown body={'| a | b |\n| - | - |\n| 1 | 2 |'} />);
    expect(document.querySelector('table thead')).not.toBeNull();
    expect(document.querySelector('table tbody')).not.toBeNull();
  });
});
```

- [ ] **Step 4.2: Run test to confirm it fails**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Markdown.test.tsx
```

Expected: FAIL — `Cannot find module './Markdown'` (or similar).

- [ ] **Step 4.3: Implement `Markdown.tsx` (Mermaid stub for now)**

Create `web/src/design-system/patterns/Markdown.tsx`:

```tsx
/**
 * Markdown — render a markdown body inside `.gl-prose`.
 *
 * Pure component. Owns the react-markdown plugin chain: GFM (tables,
 * task-lists, autolinks), rehype-highlight (syntax colors for fenced
 * code), and a `code` override that delegates `language-mermaid` to a
 * lazy-imported Mermaid pattern (added in a follow-up task — for now
 * it falls through to plain rendering).
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type { ComponentProps } from 'react';

function CodeComponent(props: ComponentProps<'code'>): JSX.Element {
  // Mermaid handling is wired in Task 5. For now: pass through.
  const { className, children, ...rest } = props;
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

export function Markdown({ body }: { body: string }): JSX.Element {
  return (
    <div className="gl-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { ignoreMissing: true, detect: true }]]}
        components={{ code: CodeComponent }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

export const meta = {
  name: 'Markdown',
  layer: 'pattern',
  import: '@/design-system/patterns/Markdown',
  variants: {},
  consumes: ['typography.body', 'components.code_block'],
  example: '<Markdown body="**hello**" />',
} as const;
```

- [ ] **Step 4.4: Run the test to confirm it passes**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Markdown.test.tsx
```

Expected: 4 tests passing.

- [ ] **Step 4.5: Commit**

```bash
git add web/src/design-system/patterns/Markdown.tsx web/src/design-system/patterns/Markdown.test.tsx
git commit -m "feat(web): add Markdown pattern (no Mermaid yet)

Renders markdown bodies inside .gl-prose with remark-gfm and
rehype-highlight. Unknown fenced languages render as plain <pre>
without throwing. GFM tables produce <thead> + <tbody>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Mermaid pattern + integrate into Markdown

**Files:**
- Create: `web/src/design-system/patterns/Mermaid.tsx`
- Modify: `web/src/design-system/patterns/Markdown.tsx`
- Modify: `web/src/design-system/patterns/Markdown.test.tsx`

- [ ] **Step 5.1: Extend the test to cover Mermaid**

Append to `web/src/design-system/patterns/Markdown.test.tsx`:

```tsx
import { vi } from 'vitest';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => {
      if (source.includes('BAD')) throw new Error('boom');
      return { svg: '<svg data-test="rendered"></svg>' };
    }),
  },
}));

describe('Markdown / Mermaid', () => {
  it('renders a mermaid block as SVG', async () => {
    render(<Markdown body={'```mermaid\nflowchart LR; A-->B\n```'} />);
    // Lazy + async render; wait for the svg to appear.
    await screen.findByTestId('rendered', undefined, { timeout: 2000 });
  });

  it('falls back to raw source when mermaid render fails', async () => {
    render(<Markdown body={'```mermaid\nflowchart LR; BAD\n```'} />);
    await screen.findByText(/flowchart LR; BAD/, undefined, { timeout: 2000 });
  });
});
```

Note: the SVG returned by the mock uses `data-test="rendered"`. Mermaid is rendered via `dangerouslySetInnerHTML`, so RTL's `getByTestId` works once the SVG is in the DOM.

- [ ] **Step 5.2: Run the new tests; confirm they fail**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Markdown.test.tsx
```

Expected: the two new tests FAIL because nothing dispatches to Mermaid yet.

- [ ] **Step 5.3: Implement `Mermaid.tsx`**

Create `web/src/design-system/patterns/Mermaid.tsx`:

```tsx
/**
 * Mermaid — render a Mermaid source string as inline SVG.
 *
 * Lazy-imported from Markdown.tsx so the ~150 KB library only loads when
 * a message body actually contains a ```mermaid block. On render error,
 * falls back to the raw source inside a styled <pre> so a malformed
 * diagram never blanks the surrounding bubble.
 *
 * Default-export so the lazy() wrapper in Markdown.tsx works as
 * intended.
 */
import { useEffect, useId, useState } from 'react';
import mermaid from 'mermaid';

let initialized = false;
function ensureInitialized(): void {
  if (initialized) return;
  initialized = true;
  mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' });
}

export default function Mermaid({ source }: { source: string }): JSX.Element {
  const [svg, setSvg] = useState<string | null>(null);
  const [errored, setErrored] = useState(false);
  const rawId = useId();
  const id = `m_${rawId.replace(/:/g, '_')}`;
  useEffect(() => {
    ensureInitialized();
    let cancelled = false;
    mermaid
      .render(id, source)
      .then(
        (r) => { if (!cancelled) setSvg(r.svg); },
        () => { if (!cancelled) setErrored(true); },
      );
    return () => { cancelled = true; };
  }, [source, id]);
  if (errored) {
    return <pre className="gl-prose-mermaid-failed">{source}</pre>;
  }
  return (
    <div
      className="gl-prose-mermaid"
      // eslint-disable-next-line react/no-danger -- mermaid output is trusted (securityLevel: 'strict' upstream)
      dangerouslySetInnerHTML={{ __html: svg ?? '' }}
    />
  );
}

export const meta = {
  name: 'Mermaid',
  layer: 'pattern',
  import: '@/design-system/patterns/Mermaid',
  variants: {},
  consumes: ['components.code_block'],
  example: '<Mermaid source="flowchart LR; A-->B" />',
} as const;
```

- [ ] **Step 5.4: Wire Mermaid into `Markdown.tsx`**

Replace the contents of `web/src/design-system/patterns/Markdown.tsx` with:

```tsx
/**
 * Markdown — render a markdown body inside `.gl-prose`.
 *
 * See spec §4.2 for the full pipeline rationale.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Suspense, lazy, type ComponentProps } from 'react';

const Mermaid = lazy(() => import('./Mermaid'));

function CodeOrMermaid(props: ComponentProps<'code'>): JSX.Element {
  const { className, children, ...rest } = props;
  const lang = /language-(\w+)/.exec(className ?? '')?.[1];
  if (lang === 'mermaid') {
    const source = String(children ?? '').replace(/\n$/, '');
    return (
      <Suspense fallback={<pre className="gl-prose-mermaid-loading">Rendering diagram…</pre>}>
        <Mermaid source={source} />
      </Suspense>
    );
  }
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

export function Markdown({ body }: { body: string }): JSX.Element {
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

export const meta = {
  name: 'Markdown',
  layer: 'pattern',
  import: '@/design-system/patterns/Markdown',
  variants: {},
  consumes: ['typography.body', 'components.code_block'],
  example: '<Markdown body="**hello**" />',
} as const;
```

- [ ] **Step 5.5: Run the full Markdown test suite**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Markdown.test.tsx
```

Expected: 6 tests passing.

- [ ] **Step 5.6: Commit**

```bash
git add web/src/design-system/patterns/Mermaid.tsx web/src/design-system/patterns/Markdown.tsx web/src/design-system/patterns/Markdown.test.tsx
git commit -m "feat(web): lazy-load Mermaid for ```mermaid fenced blocks

Markdown.tsx now dispatches code fences with language=mermaid to a
lazy-imported Mermaid pattern. On render error, falls back to the raw
source so a malformed diagram never blanks the bubble. Mermaid's
~150 KB library stays out of the initial chunk.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Swap `MessageBubble`'s inert prose div for `<Markdown>`

**Files:**
- Modify: `web/src/design-system/patterns/MessageBubble.tsx`

- [ ] **Step 6.1: Inspect current MessageBubble**

Open `web/src/design-system/patterns/MessageBubble.tsx`. Confirm the markdown-rendering block is the `<div className="prose prose-invert prose-sm text-text-primary max-w-none"><ReactMarkdown ...>{body ?? ''}</ReactMarkdown></div>` JSX at the end of the non-decline branch (around line 90–94).

- [ ] **Step 6.2: Replace the prose div with `<Markdown>`**

Edit `web/src/design-system/patterns/MessageBubble.tsx`:

- Remove the imports `import ReactMarkdown from 'react-markdown';` and `import remarkGfm from 'remark-gfm';`.
- Add the import `import { Markdown } from './Markdown';`.
- Replace this JSX block:

```tsx
<div className="prose prose-invert prose-sm text-text-primary max-w-none">
  <ReactMarkdown remarkPlugins={[remarkGfm]}>
    {body ?? ''}
  </ReactMarkdown>
</div>
```

with:

```tsx
<Markdown body={body ?? ''} />
```

Leave the rest of MessageBubble (variant shell, header line, decline branch) untouched.

- [ ] **Step 6.3: Run the test suite + build**

Run from `web/`:

```bash
npm run test -- --run
npm run build
```

Expected: all existing tests pass (MessageBubble has no test today, but `ThreadsPage.test.tsx` and `write-path.test.tsx` exercise it indirectly). Build succeeds.

- [ ] **Step 6.4: Commit**

```bash
git add web/src/design-system/patterns/MessageBubble.tsx
git commit -m "feat(web): MessageBubble renders Markdown pattern

Drops the inert prose/prose-invert wrapper (Tailwind Typography is
not installed in this repo) for the new Markdown pattern, which
applies .gl-prose styles, GFM, syntax highlighting, and lazy Mermaid.
Article shell, variant border, and header line are unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Extend `DataContext` with an `agents` domain (real + mock)

**Files:**
- Modify: `web/src/design-system/providers/DataContext.ts`
- Create: `web/src/design-system/providers/_real-agents.ts`
- Create: `web/src/design-system/providers/_mock-agents.ts`
- Modify: `web/src/design-system/providers/AppProvider.tsx`
- Modify: `web/src/design-system/providers/PrototypeProvider.tsx`
- Modify: `web/src/mocks/agents.ts`
- Create: `web/src/hooks/agents.ts`

- [ ] **Step 7.1: Extend `DataContext.ts` with `AgentsApi`**

Edit `web/src/design-system/providers/DataContext.ts`. After the `OrgsApi` block (around line 101–103), add:

```ts
// ---------------------------------------------------------------------------
// AgentsApi — minimal read-only roster used by the Composer for
// @-mention autocomplete. Lives on DataContext so prototypes can swap
// in canned fixtures.
// ---------------------------------------------------------------------------

export interface AgentsApi {
  useAgentsList: () => QueryLike<{ agents: import('@/lib/api/agents').AgentSummary[] }>;
}
```

Update `DataContextValue` to add the new field:

```ts
export interface DataContextValue {
  orgs: OrgsApi;
  agents: AgentsApi;
  threads: ThreadsApi;
  useThreadRoutes: () => ThreadRoutes;
}
```

- [ ] **Step 7.2: Implement `_real-agents.ts`**

Create `web/src/design-system/providers/_real-agents.ts`:

```ts
/**
 * Real (daemon-backed) `AgentsApi`. Private to the providers folder —
 * compositions go through `@/hooks/agents`.
 *
 * The slug is read from URL params via `useParams` so the public hook
 * shape stays provider-agnostic. Five-minute staleTime since the org
 * roster changes infrequently within a session.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { agents as agentsApi } from '@/lib/api';
import type { AgentsApi } from './DataContext';

export const realAgentsApi: AgentsApi = {
  useAgentsList: () => {
    const { slug } = useParams<{ slug: string }>();
    return useQuery({
      queryKey: ['agents', slug],
      queryFn: () => agentsApi.listAgents(slug as string),
      enabled: !!slug,
      staleTime: 5 * 60 * 1000,
    });
  },
};
```

If `web/src/lib/api/index.ts` does not yet export `agents`, also add it there alongside the existing `orgs`, `threads` exports. (Check first: `grep "^export.*agents" web/src/lib/api/index.ts` — add only if missing.)

- [ ] **Step 7.3: Implement `_mock-agents.ts`**

Create `web/src/design-system/providers/_mock-agents.ts`:

```ts
/**
 * Mock `AgentsApi` for the prototype harness. Returns canned roster
 * from `@/mocks/agents.ts`. Synchronous-ish — wrapped in useQuery to
 * mirror the real shape, so loading-state JSX in compositions still
 * runs.
 */
import { useQuery } from '@tanstack/react-query';
import type { AgentSummary } from '@/lib/api/agents';
import { MOCK_AGENTS } from '@/mocks';
import type { AgentsApi } from './DataContext';

export const mockAgentsApi: AgentsApi = {
  useAgentsList: () =>
    useQuery({
      queryKey: ['mock-agents'],
      queryFn: async (): Promise<{ agents: AgentSummary[] }> => ({ agents: MOCK_AGENTS }),
      staleTime: Infinity,
    }),
};
```

- [ ] **Step 7.4: Populate `mocks/agents.ts` with real AgentSummary entries**

Replace `web/src/mocks/agents.ts` with:

```ts
/**
 * Mock agent roster. Populated with two entries so the prototype's
 * Composer mention popup has something to render.
 */
import type { AgentSummary } from '@/lib/api/agents';

export const MOCK_AGENTS: AgentSummary[] = [
  {
    name: 'engineering_head',
    team: 'engineering',
    role: 'manager',
    executor: 'claude',
    tier: 'green',
    description: 'Owns the engineering team.',
  },
  {
    name: 'content_writer',
    team: 'content',
    role: 'worker',
    executor: 'claude',
    tier: 'green',
    description: 'Drafts posts and marketing copy.',
  },
];
```

If `web/src/mocks/index.ts` does not yet re-export `MOCK_AGENTS`, add `export { MOCK_AGENTS } from './agents';` to it.

- [ ] **Step 7.5: Wire the real provider**

Edit `web/src/design-system/providers/AppProvider.tsx`. Add the import `import { realAgentsApi } from './_real-agents';` and add `agents: realAgentsApi,` to the `DataContext.Provider` value:

```tsx
<DataContext.Provider
  value={{
    orgs: realOrgsApi,
    agents: realAgentsApi,
    threads: realThreadsApi,
    useThreadRoutes: useRealThreadRoutes,
  }}
>
```

- [ ] **Step 7.6: Wire the prototype provider**

Edit `web/src/design-system/providers/PrototypeProvider.tsx`. Add the import `import { mockAgentsApi } from './_mock-agents';` and add `agents: mockAgentsApi,` to its `DataContext.Provider` value (mirroring the AppProvider edit above).

- [ ] **Step 7.7: Create the public hook**

Create `web/src/hooks/agents.ts`:

```ts
/**
 * Public, provider-aware agents hooks. Compositions import from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useAgentsList: ReturnType<typeof useData>['agents']['useAgentsList'] = () =>
  useData().agents.useAgentsList();
```

- [ ] **Step 7.8: Type-check + build**

Run from `web/`:

```bash
npm run typecheck
npm run build
```

Expected: both pass. Build step also re-runs `build:registry` — confirm it picks up the new Markdown / Mermaid `meta` blocks without error.

- [ ] **Step 7.9: Commit**

```bash
git add web/src/design-system/providers/DataContext.ts \
        web/src/design-system/providers/_real-agents.ts \
        web/src/design-system/providers/_mock-agents.ts \
        web/src/design-system/providers/AppProvider.tsx \
        web/src/design-system/providers/PrototypeProvider.tsx \
        web/src/mocks/agents.ts \
        web/src/mocks/index.ts \
        web/src/hooks/agents.ts \
        web/src/lib/api/index.ts
git commit -m "feat(web): add agents domain to DataContext (real + mock)

Extends the provider pattern so Composer's @-mention autocomplete
can read the org roster under both <AppProvider> and
<PrototypeProvider>. Public hook is useAgentsList() — slug stays
inside the provider, consistent with useThreadsList and useOrgsList.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(Only add `web/src/mocks/index.ts` and `web/src/lib/api/index.ts` to the commit if they were edited in steps 7.2 and 7.4.)

---

## Task 8: MentionAutocomplete pattern

**Files:**
- Create: `web/src/design-system/patterns/MentionAutocomplete.tsx`
- Create: `web/src/design-system/patterns/MentionAutocomplete.test.tsx`

- [ ] **Step 8.1: Write the failing test**

Create `web/src/design-system/patterns/MentionAutocomplete.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { AgentSummary } from '@/lib/api/agents';
import { MentionAutocomplete } from './MentionAutocomplete';

const AGENTS: AgentSummary[] = [
  { name: 'engineering_head', team: 'engineering', role: 'manager', executor: 'claude', tier: 'green', description: null },
  { name: 'content_writer',   team: 'content',     role: 'worker',  executor: 'claude', tier: 'green', description: null },
  { name: 'design_lead',      team: 'design',      role: 'manager', executor: 'claude', tier: 'green', description: null },
];

const ANCHOR = { x: 100, y: 100, width: 200, height: 24 };

describe('MentionAutocomplete', () => {
  it('renders matching agents filtered by query prefix', () => {
    const onSelect = vi.fn();
    const onDismiss = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        query="eng"
        agents={AGENTS}
        onSelect={onSelect}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
    expect(screen.queryByText('content_writer')).toBeNull();
  });

  it('Esc fires onDismiss', async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        query=""
        agents={AGENTS}
        onSelect={vi.fn()}
        onDismiss={onDismiss}
      />,
    );
    await user.keyboard('{Escape}');
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('Enter selects the active item', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        query=""
        agents={AGENTS}
        onSelect={onSelect}
        onDismiss={vi.fn()}
      />,
    );
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith(AGENTS[0]);
  });

  it('ArrowDown moves the active item then Enter selects it', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionAutocomplete
        anchor={ANCHOR}
        query=""
        agents={AGENTS}
        onSelect={onSelect}
        onDismiss={vi.fn()}
      />,
    );
    await user.keyboard('{ArrowDown}{Enter}');
    expect(onSelect).toHaveBeenCalledWith(AGENTS[1]);
  });
});
```

- [ ] **Step 8.2: Confirm the test fails**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/MentionAutocomplete.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 8.3: Implement `MentionAutocomplete.tsx`**

Create `web/src/design-system/patterns/MentionAutocomplete.tsx`:

```tsx
/**
 * MentionAutocomplete — floating popup of matching agents, anchored
 * below a caret rect.
 *
 * Pure props in / events out. No fetching — the caller passes the full
 * agent list and the current query string. Keyboard handling is global
 * (document-level keydown) because the popup is rendered outside the
 * focused textarea and we need ArrowUp/Down/Enter/Esc to interact with
 * it without stealing focus.
 *
 * Filtering: prefix-match on agent.name (case-insensitive).
 */
import { useEffect, useMemo, useState } from 'react';
import type { AgentSummary } from '@/lib/api/agents';

export interface MentionAutocompleteProps {
  anchor: { x: number; y: number; width: number; height: number };
  query: string;
  agents: AgentSummary[];
  onSelect: (agent: AgentSummary) => void;
  onDismiss: () => void;
}

export function MentionAutocomplete({
  anchor,
  query,
  agents,
  onSelect,
  onDismiss,
}: MentionAutocompleteProps): JSX.Element | null {
  const matches = useMemo(() => {
    const q = query.toLowerCase();
    return agents.filter((a) => a.name.toLowerCase().startsWith(q)).slice(0, 8);
  }, [query, agents]);

  const [active, setActive] = useState(0);
  // Reset active index when the query (and therefore matches) changes.
  useEffect(() => { setActive(0); }, [query, agents.length]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onDismiss(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, matches.length - 1)); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); return; }
      if ((e.key === 'Enter' || e.key === 'Tab') && matches[active]) {
        e.preventDefault();
        onSelect(matches[active]);
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [matches, active, onSelect, onDismiss]);

  if (matches.length === 0) return null;

  const style: React.CSSProperties = {
    position: 'fixed',
    left: anchor.x,
    top: anchor.y + anchor.height + 4,
    minWidth: 200,
    maxWidth: 320,
    zIndex: 1000,
  };

  return (
    <div
      role="listbox"
      aria-label="Mention agents"
      style={style}
      className="border-border-default bg-surface-overlay text-text-primary text-caption rounded-md border shadow-lg"
    >
      {matches.map((a, i) => (
        <button
          key={a.name}
          type="button"
          role="option"
          aria-selected={i === active}
          onMouseDown={(e) => { e.preventDefault(); onSelect(a); }}
          onMouseEnter={() => setActive(i)}
          className={`block w-full px-3 py-1.5 text-left ${
            i === active ? 'bg-accent-muted' : 'hover:bg-surface-raised'
          }`}
        >
          <span className="font-medium">{a.name}</span>
          <span className="text-text-muted ml-2">{a.team}</span>
        </button>
      ))}
    </div>
  );
}

export const meta = {
  name: 'MentionAutocomplete',
  layer: 'pattern',
  import: '@/design-system/patterns/MentionAutocomplete',
  variants: {},
  consumes: ['components.popover'],
  example: "<MentionAutocomplete anchor={{x:0,y:0,width:0,height:0}} query='' agents={[]} onSelect={() => {}} onDismiss={() => {}} />",
} as const;
```

- [ ] **Step 8.4: Run the test, confirm it passes**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/MentionAutocomplete.test.tsx
```

Expected: 4 tests passing.

- [ ] **Step 8.5: Commit**

```bash
git add web/src/design-system/patterns/MentionAutocomplete.tsx web/src/design-system/patterns/MentionAutocomplete.test.tsx
git commit -m "feat(web): MentionAutocomplete pattern

Floating popup over a caret rect listing prefix-matched agents.
Pure props/events; arrow keys + Enter/Tab/Esc handled at the
document level so the popup interacts with a focused textarea
without stealing focus.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `useAutoGrow` + `useThreadDraft` (composer-local hooks)

We keep these alongside `Composer.tsx` (file-local) rather than in `@/hooks/` because they have no provider-aware indirection and exist purely for Composer.

**Files:**
- Modify: `web/src/design-system/patterns/Composer.tsx` (add hook definitions)
- Create: `web/src/design-system/patterns/Composer.test.tsx` (just the draft / autogrow tests in this task; mention tests in Task 10)

- [ ] **Step 9.1: Write the failing tests (drafts + autogrow)**

Create `web/src/design-system/patterns/Composer.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from './Composer';

const NOOP_SEND = vi.fn(async () => {});

beforeEach(() => {
  localStorage.clear();
  NOOP_SEND.mockClear();
});

afterEach(() => {
  localStorage.clear();
});

describe('Composer / drafts', () => {
  it('restores a saved draft on mount', () => {
    localStorage.setItem('grassland:draft:test-org:THR-001', 'in-progress text');
    // Note: useThreadDraft needs orgSlug. Composer reads it via useOrgSlug()
    // (the codebase already wires <OrgProvider> in routes; tests need it too).
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-001"
          pending={false}
          onSend={NOOP_SEND}
        />
      </WithOrgSlug>,
    );
    expect(screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i }).value)
      .toBe('in-progress text');
  });

  it('clears the draft after a successful send', async () => {
    const user = userEvent.setup();
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-001"
          pending={false}
          onSend={NOOP_SEND}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hello');
    // Debounced write — wait the 300ms.
    await new Promise((r) => setTimeout(r, 320));
    expect(localStorage.getItem('grassland:draft:test-org:THR-001')).toBe('hello');
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(NOOP_SEND).toHaveBeenCalled();
    expect(localStorage.getItem('grassland:draft:test-org:THR-001')).toBeNull();
  });
});

// Helper: wraps children in a StaticOrgProvider so Composer's
// useOrgSlug() resolves to a known slug under jsdom.
import { StaticOrgProvider } from '@/lib/orgSlug';
function WithOrgSlug({ slug, children }: { slug: string; children: React.ReactNode }) {
  return <StaticOrgProvider slug={slug}>{children}</StaticOrgProvider>;
}
```

(Check first that `StaticOrgProvider` exists in `@/lib/orgSlug` — `grep -n 'StaticOrgProvider' web/src/lib/orgSlug.ts`. The codebase reference in PrototypeProvider.tsx confirms it does.)

- [ ] **Step 9.2: Confirm the tests fail**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Composer.test.tsx
```

Expected: FAIL — Composer still uses its old prop shape and has no draft hook.

- [ ] **Step 9.3: Add `useAutoGrow` + `useThreadDraft` to Composer.tsx**

Edit `web/src/design-system/patterns/Composer.tsx`. Add the hook definitions near the top of the file (after imports, before `ComposerProps`):

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { useOrgSlug } from '@/lib/orgSlug';

const MAX_TEXTAREA_PX = 240;
const DRAFT_CAP_CHARS = 65_536;
const DRAFT_DEBOUNCE_MS = 300;

function useAutoGrow(
  ref: React.RefObject<HTMLTextAreaElement>,
  value: string,
  maxPx = MAX_TEXTAREA_PX,
): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, maxPx) + 'px';
  }, [ref, value, maxPx]);
}

interface DraftHandle {
  draft: string;
  setDraft: (next: string) => void;
  clearDraft: () => void;
}

function useThreadDraft(orgSlug: string, threadId: string): DraftHandle {
  const key = `grassland:draft:${orgSlug}:${threadId}`;
  const [draft, setDraftState] = useState<string>(() => {
    try { return localStorage.getItem(key) ?? ''; } catch { return ''; }
  });
  const timer = useRef<number | null>(null);

  // Re-read when key changes (org/thread switch).
  useEffect(() => {
    try { setDraftState(localStorage.getItem(key) ?? ''); } catch { setDraftState(''); }
  }, [key]);

  const setDraft = useCallback((next: string) => {
    setDraftState(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      try {
        if (next.length > DRAFT_CAP_CHARS) { console.debug('draft cap exceeded; skipping persist'); return; }
        if (next === '') localStorage.removeItem(key);
        else localStorage.setItem(key, next);
      } catch (e) {
        console.debug('draft persist failed', e);
      }
    }, DRAFT_DEBOUNCE_MS);
  }, [key]);

  const clearDraft = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    try { localStorage.removeItem(key); } catch { /* ignore */ }
    setDraftState('');
  }, [key]);

  return { draft, setDraft, clearDraft };
}
```

- [ ] **Step 9.4: Update Composer's signature + body to use the hooks**

Replace the `ComposerProps` interface and the `Composer` function body in `web/src/design-system/patterns/Composer.tsx` with:

```tsx
interface ComposerProps {
  disabled?: boolean;
  pending?: boolean;
  errorMessage?: string | null;
  helper?: string;
  placeholder?: string;
  registerFocus?: (focus: () => void) => void;
  onSend: (markdown: string, addressedTo: string[]) => unknown | Promise<unknown>;

  // NEW (required)
  agents: import('@/lib/api/agents').AgentSummary[];
  threadId: string;
}

export function Composer({
  disabled,
  pending,
  errorMessage,
  helper,
  placeholder,
  registerFocus,
  onSend,
  agents,           // wired in Task 10
  threadId,
}: ComposerProps): JSX.Element {
  const orgSlug = useOrgSlug();
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(textareaRef, draft);

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  const submit = async () => {
    if (!draft.trim() || disabled || pending) return;
    try {
      // addressedTo is wired in Task 10; for now always @all.
      await onSend(draft, ['@all']);
      clearDraft();
    } catch {
      // Composition surfaces via errorMessage; draft is preserved for retry.
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={
          placeholder ?? (disabled ? 'Thread is closed.' : 'Write a message… Cmd/Ctrl+Enter to send.')
        }
        disabled={disabled || pending}
        rows={3}
        aria-label="Compose follow-up"
        className="border-border-default bg-surface-raised text-body-lg text-text-primary placeholder:text-text-muted focus:border-accent-default w-full resize-none rounded-md border px-3 py-2 focus:outline-none disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-2">
        {errorMessage ? (
          <span className="text-caption text-feedback-danger">{errorMessage}</span>
        ) : (
          <span className="text-caption text-text-muted">{helper ?? ''}</span>
        )}
        <Button onClick={submit} disabled={disabled || !draft.trim() || pending}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
```

Note the change from `rows={4}` to `rows={3}` — the textarea now grows past that automatically via `useAutoGrow`. Initial height is the minimum.

- [ ] **Step 9.5: Run the tests + typecheck**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Composer.test.tsx
npm run typecheck
```

Expected: 2 draft tests pass. Typecheck FAILS in `ThreadsPage.tsx` because the existing call site doesn't pass `agents` or `threadId` — fix in Task 11. (Note: Task 11 unblocks this; do not commit yet.)

- [ ] **Step 9.6: Temporarily ignore the type errors and commit (composition fix follows in Task 11)**

The composition error is intentional — see spec §3.2 ("typed compile-time error to surface the wiring"). We do not want to introduce a fallback; we want the typecheck to drive us to wire `ThreadsPage.tsx`. To keep this task committable while still verifying the new Composer works in isolation, we commit the test + Composer changes here and accept that `npm run typecheck` is RED at this commit — Task 11 turns it green.

```bash
git add web/src/design-system/patterns/Composer.tsx web/src/design-system/patterns/Composer.test.tsx
git commit -m "feat(web): Composer autogrow + draft persistence + new props

Adds useAutoGrow + useThreadDraft (file-local hooks). Composer now
auto-resizes up to 240px, persists drafts per (org_slug, thread_id)
in localStorage with a 300ms debounce and 65,536-char cap, and
gains required props agents + threadId. onSend signature gains a
second addressedTo arg; mention wiring lands in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Composer mention integration + addressedTo wiring

**Files:**
- Modify: `web/src/design-system/patterns/Composer.tsx`
- Modify: `web/src/design-system/patterns/Composer.test.tsx`

- [ ] **Step 10.1: Write the failing mention tests**

Append to `web/src/design-system/patterns/Composer.test.tsx`:

```tsx
import type { AgentSummary } from '@/lib/api/agents';

const TEST_AGENTS: AgentSummary[] = [
  { name: 'design_lead',  team: 'design', role: 'manager', executor: 'claude', tier: 'green', description: null },
  { name: 'design_dev_1', team: 'design', role: 'worker',  executor: 'claude', tier: 'green', description: null },
];

describe('Composer / mentions', () => {
  it('typing @de opens the autocomplete', async () => {
    const user = userEvent.setup();
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-002"
          pending={false}
          onSend={vi.fn(async () => {})}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, '@de');
    expect(await screen.findByText('design_lead')).toBeInTheDocument();
    expect(screen.getByText('design_dev_1')).toBeInTheDocument();
  });

  it('selecting an agent inserts @name and sends with addressedTo set', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-003"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'hi @de');
    await user.keyboard('{Enter}'); // selects first match: design_lead
    expect(ta.value).toBe('hi @design_lead ');
    await user.type(ta, 'please review');
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(onSend).toHaveBeenCalledWith('hi @design_lead please review', ['design_lead']);
  });

  it('send with no mentions falls back to @all', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={TEST_AGENTS}
          threadId="THR-004"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'plain message');
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(onSend).toHaveBeenCalledWith('plain message', ['@all']);
  });

  it('literal @all is recognized regardless of agents list', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => {});
    render(
      <WithOrgSlug slug="test-org">
        <Composer
          agents={[]}
          threadId="THR-005"
          pending={false}
          onSend={onSend}
        />
      </WithOrgSlug>,
    );
    const ta = screen.getByRole<HTMLTextAreaElement>('textbox', { name: /compose/i });
    await user.type(ta, 'heads-up @all');
    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(onSend).toHaveBeenCalledWith('heads-up @all', ['@all']);
  });
});
```

- [ ] **Step 10.2: Confirm tests fail**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Composer.test.tsx
```

Expected: the four new tests FAIL.

- [ ] **Step 10.3: Implement mention logic in `Composer.tsx`**

Edit `web/src/design-system/patterns/Composer.tsx`. Add the import:

```tsx
import { MentionAutocomplete } from './MentionAutocomplete';
import type { AgentSummary } from '@/lib/api/agents';
```

Add these helpers above `Composer`:

```tsx
const MENTION_TOKEN_RE = /@([A-Za-z0-9_-]+)/g;

function detectOpenMention(text: string, caret: number):
  | { query: string; tokenStart: number }
  | null {
  for (let i = caret - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === '@') return { query: text.slice(i + 1, caret), tokenStart: i };
    if (/\s/.test(ch)) return null;
  }
  return null;
}

function resolveAddressedTo(body: string, agents: AgentSummary[]): string[] {
  const byName = new Set(agents.map((a) => a.name));
  const out = new Set<string>();
  for (const m of body.matchAll(MENTION_TOKEN_RE)) {
    const token = m[1];
    if (token === 'all') { out.add('@all'); continue; }
    if (byName.has(token)) out.add(token);
  }
  return out.size > 0 ? Array.from(out) : ['@all'];
}
```

Replace the existing `Composer` body with a version that wires the popup and `resolveAddressedTo` into `submit`. The full replacement:

```tsx
export function Composer({
  disabled,
  pending,
  errorMessage,
  helper,
  placeholder,
  registerFocus,
  onSend,
  agents,
  threadId,
}: ComposerProps): JSX.Element {
  const orgSlug = useOrgSlug();
  const { draft, setDraft, clearDraft } = useThreadDraft(orgSlug, threadId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(textareaRef, draft);

  const [mention, setMention] = useState<
    | { query: string; tokenStart: number; anchor: { x: number; y: number; width: number; height: number } }
    | null
  >(null);

  useEffect(() => {
    registerFocus?.(() => textareaRef.current?.focus());
  }, [registerFocus]);

  const refreshMention = useCallback(() => {
    const el = textareaRef.current;
    if (!el || disabled) { setMention(null); return; }
    const caret = el.selectionStart ?? 0;
    const m = detectOpenMention(draft, caret);
    if (!m) { setMention(null); return; }
    const rect = el.getBoundingClientRect();
    // v1: anchor the popup to the textarea's bottom-left corner.
    setMention({ query: m.query, tokenStart: m.tokenStart, anchor: { x: rect.left, y: rect.top, width: rect.width, height: rect.height } });
  }, [draft, disabled]);

  useEffect(() => { refreshMention(); }, [refreshMention]);

  const acceptMention = useCallback((agent: AgentSummary) => {
    if (!mention) return;
    const el = textareaRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? 0;
    const before = draft.slice(0, mention.tokenStart);
    const after = draft.slice(caret);
    const inserted = `@${agent.name} `;
    const next = before + inserted + after;
    setDraft(next);
    setMention(null);
    // Restore caret position right after the inserted token.
    requestAnimationFrame(() => {
      const newCaret = (before + inserted).length;
      el.setSelectionRange(newCaret, newCaret);
      el.focus();
    });
  }, [draft, mention, setDraft]);

  const submit = async () => {
    if (!draft.trim() || disabled || pending) return;
    const addressedTo = resolveAddressedTo(draft, agents);
    try {
      await onSend(draft, addressedTo);
      clearDraft();
      setMention(null);
    } catch {
      // Composition surfaces via errorMessage; draft is preserved for retry.
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyUp={refreshMention}
        onClick={refreshMention}
        onKeyDown={(e) => {
          // Cmd/Ctrl+Enter submits IF the mention popup is closed; otherwise
          // the popup handles Enter (via its own document-level keydown).
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !mention) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={
          placeholder ?? (disabled ? 'Thread is closed.' : 'Write a message… Cmd/Ctrl+Enter to send.')
        }
        disabled={disabled || pending}
        rows={3}
        aria-label="Compose follow-up"
        className="border-border-default bg-surface-raised text-body-lg text-text-primary placeholder:text-text-muted focus:border-accent-default w-full resize-none rounded-md border px-3 py-2 focus:outline-none disabled:opacity-50"
      />
      {mention && (
        <MentionAutocomplete
          anchor={mention.anchor}
          query={mention.query}
          agents={agents}
          onSelect={acceptMention}
          onDismiss={() => setMention(null)}
        />
      )}
      <div className="flex items-center justify-between gap-2">
        {errorMessage ? (
          <span className="text-caption text-feedback-danger">{errorMessage}</span>
        ) : (
          <span className="text-caption text-text-muted">{helper ?? ''}</span>
        )}
        <Button onClick={submit} disabled={disabled || !draft.trim() || pending}>
          {pending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 10.4: Run the test suite for Composer**

Run from `web/`:

```bash
npm run test -- --run src/design-system/patterns/Composer.test.tsx
```

Expected: all 6 tests pass.

- [ ] **Step 10.5: Commit**

```bash
git add web/src/design-system/patterns/Composer.tsx web/src/design-system/patterns/Composer.test.tsx
git commit -m "feat(web): Composer @-mention autocomplete + addressedTo wiring

Detects open @-mentions while typing, anchors MentionAutocomplete to
the textarea, inserts @<name> on select. On send, regex-scans the
final body and computes addressed_to = [matched-agent names] (or
['@all'] if no mentions; literal @all also resolves to '@all'). The
popup swallows Enter while open, so Cmd/Ctrl+Enter only submits when
the popup is closed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Wire `ThreadsPage` to pass `agents` + `threadId` + `addressedTo`

This is the change that turns `npm run typecheck` green again.

**Files:**
- Modify: `web/src/features/threads/ThreadsPage.tsx`

- [ ] **Step 11.1: Read the current Composer call site**

Open `web/src/features/threads/ThreadsPage.tsx`. The Composer is instantiated inside `DetailColumn`'s `composer` prop, around lines 243–252:

```tsx
composer={
  <Composer
    disabled={activeThread.data?.status !== 'open'}
    pending={sendFollowUp.isPending}
    errorMessage={composerError}
    helper="Sends as founder; @all by default."
    onSend={onSendFollowUp}
    registerFocus={(focus) => { composerFocusRef.current = focus; }}
  />
}
```

`onSendFollowUp` is defined around line 117–131; today it calls `sendFollowUp.mutateAsync({ body_markdown: markdown, addressed_to: ['@all'] })`.

- [ ] **Step 11.2: Add the agents query at the top of `ThreadsPage`**

In the imports block of `ThreadsPage.tsx`, add:

```tsx
import { useAgentsList } from '@/hooks/agents';
```

Inside the `ThreadsPage` function body, near the other queries (after `useThreadsInboxSSE();` and before the `threads = useMemo(...)`):

```tsx
const agentsQuery = useAgentsList();
const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
```

- [ ] **Step 11.3: Update `onSendFollowUp` to accept and forward `addressedTo`**

Replace the existing `onSendFollowUp` definition with:

```tsx
const onSendFollowUp = async (markdown: string, addressedTo: string[]) => {
  if (!threadId) return;
  setComposerError(null);
  try {
    await sendFollowUp.mutateAsync({ body_markdown: markdown, addressed_to: addressedTo });
  } catch (err) {
    if (err instanceof ApiError) {
      setComposerError(describeError(err.code, `HTTP ${err.status}`));
    } else {
      setComposerError(String(err));
    }
    throw err;
  }
};
```

- [ ] **Step 11.4: Pass `agents` + `threadId` into `<Composer>`**

Replace the `composer={...}` block with:

```tsx
composer={
  <Composer
    agents={agents}
    threadId={threadId ?? ''}
    disabled={activeThread.data?.status !== 'open'}
    pending={sendFollowUp.isPending}
    errorMessage={composerError}
    helper="Sends as founder; @all by default — type @ to mention an agent."
    onSend={onSendFollowUp}
    registerFocus={(focus) => { composerFocusRef.current = focus; }}
  />
}
```

(`threadId` is `string | undefined` from the route; we already gate the entire `DetailColumn` on `threadId`, so the fallback `''` is dead code but satisfies the prop's `string` type.)

- [ ] **Step 11.5: Run the full test suite + typecheck + build**

Run from `web/`:

```bash
npm run typecheck
npm run test -- --run
npm run build
```

Expected: typecheck green, every test green, build succeeds.

If `ThreadsPage.test.tsx` fails because it constructs Composer indirectly without an `agents` prop being mocked — verify the mock provider's `useAgentsList` is returning the empty / canned list. Update the test only if the failure is real (i.e., the page rendered without Composer mounting) — do not edit the test to suppress signal.

- [ ] **Step 11.6: Commit**

```bash
git add web/src/features/threads/ThreadsPage.tsx
git commit -m "feat(web): wire ThreadsPage to new Composer

Threads agents (via useAgentsList()) and threadId into <Composer>,
forwards addressed_to from the Composer's send callback into
useSendFollowUp. Helper line updated to advertise @-mentions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Live verification + final summary

This is the verification-before-completion checkpoint. No code changes; we exercise the running app per acceptance criteria.

- [ ] **Step 12.1: Rebuild the bundle**

```bash
scripts/build_web.sh
```

Expected: bundle written to `web/dist/`; ~+45 KB on the initial gz chunk vs main.

- [ ] **Step 12.2: Open THR-003 via playwright-cli and assert layout**

```bash
playwright-cli open --browser=chrome
playwright-cli resize 1440 900
playwright-cli goto "http://localhost:$(cat ~/.grassland/daemon.port)/orgs/tourism-org/threads/THR-003"
playwright-cli --raw eval "() => JSON.stringify({
  mainScrollLeft: document.querySelector('main')?.scrollLeft,
  asideWidth: document.querySelector('aside')?.clientWidth,
  asideX: document.querySelector('aside')?.getBoundingClientRect().x,
}, null, 2)"
```

Expected:

```json
{
  "mainScrollLeft": 0,
  "asideWidth": 320,
  "asideX": 0
}
```

If any value diverges, return to Task 1 / Task 3 and re-verify the grid change and CSS overflow rules.

- [ ] **Step 12.3: Confirm code blocks scroll internally**

```bash
playwright-cli --raw eval "() => {
  const pre = document.querySelector('main section .gl-prose pre');
  if (!pre) return 'NO-PRE';
  const cs = getComputedStyle(pre);
  pre.scrollLeft = 200;
  return JSON.stringify({
    overflowX: cs.overflowX,
    preScrollLeft: pre.scrollLeft,
    mainScrollLeft: document.querySelector('main')?.scrollLeft,
  }, null, 2);
}"
```

Expected: `overflowX: "auto"`, `preScrollLeft > 0`, `mainScrollLeft: 0`. Code block scrolled, page did not.

- [ ] **Step 12.4: Smoke-test mermaid via the prototype sandbox**

```bash
playwright-cli goto "http://localhost:$(cat ~/.grassland/daemon.port)/__prototypes/threads-v2"
playwright-cli --raw eval "() => Boolean(document.querySelector('.gl-prose-mermaid svg'))"
```

If the prototype fixtures don't include a mermaid block, instead drop a temporary message into the dev daemon by calling `grassland threads compose` with a body that contains a ` ```mermaid\nflowchart LR; A-->B\n``` ` block, then load that thread.

Expected: returns `true` (an SVG rendered).

- [ ] **Step 12.5: Composer smoke test**

In the open browser at THR-003 (status: `open`), drive the composer manually:

```bash
playwright-cli snapshot                       # capture refs
# fill the textarea ref (whatever ref the snapshot reports for the
# 'Compose follow-up' textarea)
playwright-cli type "@de"
playwright-cli snapshot                       # confirm 'design_lead' (or other) appears in the popup
playwright-cli press Escape
playwright-cli close
```

Expected: the snapshot after `@de` lists matching agent names. Esc dismisses.

- [ ] **Step 12.6: Final commit (none expected — only verify)**

```bash
git status
```

Expected: clean tree. Everything since Task 1 already committed.

- [ ] **Step 12.7: Summarize the acceptance-criteria pass**

Write up a brief summary message confirming the six items from spec §9 (Sidebar visible / Code blocks scroll / Mermaid renders / Mentions wire to addressed_to / Send key + draft / No regressions). If any fail, return to the task most likely responsible per the spec mapping and re-iterate.

---

## Self-review checklist

- **Spec coverage:**
  - §1 Problem — surfaced in commit messages, not implemented.
  - §2 Goals — Task 1 (sidebar), Task 4–5 (markdown render), Task 9–10 (composer), §3.2 wire — Task 11.
  - §3.1 New files — all 8 covered (Task 3 CSS, Task 4–5 Markdown+Mermaid, Task 8 MentionAutocomplete, Task 9 Composer.test, Task 7 providers + hook).
  - §3.2 Edited files — all covered (Task 1 ThreadsLayout, Task 6 MessageBubble, Tasks 9–10 Composer, Task 11 ThreadsPage, Task 3 styles.css, Task 7 DataContext + providers, Task 2 package.json).
  - §4 Markdown pipeline — Task 4 baseline, Task 5 Mermaid.
  - §5 Composer v2 — Tasks 9–10.
  - §6 Layout fix — Task 1.
  - §7 Tests — Task 4, 5, 8, 9, 10. Spec also asks for an integration regression sentinel in ThreadsPage.test.tsx; in practice JSDOM does not compute layout (clientWidth is not reliable), so we substitute live playwright-cli verification in Task 12. **Documented divergence**: the spec's §7.2 integration test is replaced with manual playwright verification in this plan, on the grounds that JSDOM cannot exercise the underlying CSS Grid behavior. If the executing engineer disagrees, they can attempt the JSDOM version and remove the manual step.
  - §8 Rollout, §9 Acceptance, §10 Non-goals — verification in Task 12.

- **Placeholder scan:** every step has actual code or actual commands. No "implement appropriate X". No "TODO". No "similar to Task N" without a code block.

- **Type consistency:** `AgentSummary` is the type throughout (imports from `@/lib/api/agents`). `useAgentsList()` signature is `() => QueryLike<{ agents: AgentSummary[] }>` in DataContext, the hook re-export, and the call sites. `onSend` is `(markdown: string, addressedTo: string[]) => unknown | Promise<unknown>` in Composer props and ThreadsPage's `onSendFollowUp`. `useThreadDraft(orgSlug, threadId)` matches the call site in Composer.

If you find any issue during execution, fix it inline in the affected step and continue — do not re-plan.
