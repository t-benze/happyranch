import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import corpus from '../../../../protocol/thread-mention-corpus.json';
import { Markdown } from './Markdown';
import mermaid from 'mermaid';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => {
      if (source.includes('BAD')) throw new Error('boom');
      return { svg: '<svg data-testid="rendered"></svg>' };
    }),
  },
}));

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

  // THR-337: markup typed on the opening fence line was parsed as lang/meta, leaving an empty block.
  it('shows markup typed on the opening fence line as literal code', () => {
    const anchor = '<a href="https://beian.mps.gov.cn/#/query/webSearch?code=44030002018973" rel="noreferrer" target="_blank">粤公网安备44030002018973号</a>';
    const view = render(<Markdown body={'```' + anchor + '\n```\n\n深圳公安备案已完成，添加到网站山\n\n```ts\nconst x = 1;\n```'} />);
    const [recovered, ordinary] = view.container.querySelectorAll('pre code');
    expect(recovered.textContent).toBe(anchor + '\n');
    expect(recovered.className).not.toMatch(/language-</);
    expect(view.container.querySelector('a, script, [onclick]')).toBeNull();
    expect(screen.getByText('深圳公安备案已完成，添加到网站山')).toBeInTheDocument();
    expect(ordinary.className).toMatch(/language-ts/);
    expect(ordinary.textContent).toBe('const x = 1;\n');
  });

  it.each([
    ['~~~<b>x</b>\nbody\n~~~', '<b>x</b>\nbody\n', null],
    ['```html\n<a href="x">y</a>\n```', '<a href="x">y</a>\n', 'language-html'],
    ['```ts title="a b"\nlet y\n```', 'let y\n', 'language-ts'],
    ['```\n```', '', null],
    ['```\n<a href="x">y</a>\n```', '<a href="x">y</a>\n', null],
  ])('keeps fence semantics for %j', (body, text, lang) => {
    const view = render(<Markdown body={body} />);
    const code = view.container.querySelector('pre code')!;
    expect(code.textContent).toBe(text);
    if (lang) expect(code.className).toMatch(lang);
    expect(view.container.querySelector('.gl-prose a, .gl-prose b')).toBeNull();
  });

  it('renders a GFM table with thead and tbody', () => {
    render(<Markdown body={'| a | b |\n| - | - |\n| 1 | 2 |'} />);
    expect(document.querySelector('table thead')).not.toBeNull();
    expect(document.querySelector('table tbody')).not.toBeNull();
  });
});

describe('Markdown / Mermaid', () => {
  it('renders a mermaid block as SVG', async () => {
    render(<Markdown body={'```mermaid\nflowchart LR; A-->B\n```'} />);
    // Lazy + async render; wait for the svg to appear.
    await screen.findByTestId('rendered', undefined, { timeout: 2000 });
  });

  it('falls back to raw source when mermaid render fails', async () => {
    const body = '```mermaid\nflowchart LR; BAD\n```';
    const view = render(<Markdown body={body} />);
    const raw = await screen.findByText(/flowchart LR; BAD/, undefined, { timeout: 2000 });
    const calls = vi.mocked(mermaid.render).mock.calls.length;
    for (const label of ['正在渲染图表…', 'Rendering diagram…']) {
      view.rerender(<Markdown body={body} mermaidLoadingLabel={label} />);
      expect(screen.getByText(/flowchart LR; BAD/)).toBe(raw);
    }
    expect(vi.mocked(mermaid.render).mock.calls.length).toBe(calls);
  });
});

// C10/C21: real ReactMarkdown source mapping; syntax marks only, never delivery proof.
describe('thread-only source syntax styling', () => {
  it.each([
    ['@Current @Current', ['@Current','@Current']],
    ['😀 @Current', ['@Current']],
    ['`@Former`\n> @Former\n\\@Former', []],
    ['*@Current* [@Other](https://example.invalid/)', ['@Other']],
    ['@Cur&#114;ent', []],
    ['> @Former\n@Current', []], // renderer merges raw lines; exact text mapping unavailable
    ['> @Former\n\n@Current', ['@Current']],
    ['```\n@Current', []],
    ['    > @Current', []],
  ])('styles proven raw occurrences in %s', (body, marks) => {
    const view = render(<Markdown body={body as string} threadMentions />);
    expect([...view.container.querySelectorAll('[data-thread-mention]')].map(n=>n.textContent)).toEqual(marks);
    expect(view.container.querySelector('[data-thread-mention] a')).toBeNull();
    view.rerender(<Markdown body={body as string} />);
    expect(view.container.querySelector('[data-thread-mention]')).toBeNull();
  });
});

// Every shared raw input crosses the shipping opt-in renderer. Container/plain
// fallback is deliberate; marks may only contain a token present in raw ranges.
describe('shared corpus render conservation',()=>{
  it.each(corpus.cases)('$id never invents a decoded mention',row=>{
    const view=render(<Markdown body={row.input} threadMentions />);
    const tokens=new Set(row.expected_ranges.map(r=>'@'+r.token));
    for(const mark of view.container.querySelectorAll('[data-thread-mention]')) {
      expect(tokens.has(mark.textContent!)).toBe(true);
      expect(mark.closest('code,pre')).toBeNull();
    }
  });
});
