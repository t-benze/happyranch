import { render, screen, act, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Markdown } from './Markdown';
import mermaid from 'mermaid';

const chunk = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release };
});
// Delay the external library import; the real lazy boundary stays in production.
vi.mock('./Mermaid', async (importOriginal) => {
  await chunk.ready;
  return importOriginal<typeof import('./Mermaid')>();
});

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

  it('renders a GFM table with thead and tbody', () => {
    render(<Markdown body={'| a | b |\n| - | - |\n| 1 | 2 |'} />);
    expect(document.querySelector('table thead')).not.toBeNull();
    expect(document.querySelector('table tbody')).not.toBeNull();
  });
});

describe('Markdown / Mermaid', () => {
  it('pending blocks localize in place, keep authored copy raw and preserve loaded diagram identity', async () => {
    const body = 'Rendering diagram…\n\n正在渲染图表…\n\n```text\nRendering diagram…\n正在渲染图表…\n```\n\n```mermaid\nflowchart LR; A-->B\n```\n\n```mermaid\nflowchart LR; C-->D\n```';
    const view = render(<Markdown body={body} />);
    const prose = view.container.querySelector('.gl-prose');
    const paragraph = screen.getByText('Rendering diagram…', { selector: 'p' });
    const code = view.container.querySelector('pre code');
    const fallbacks = Array.from(view.container.querySelectorAll('.gl-prose-mermaid-loading'));
    try {
      expect(fallbacks).toHaveLength(2);
      expect(fallbacks.map(node => node.textContent)).toEqual(['Rendering diagram…', 'Rendering diagram…']);
      for (const label of ['正在渲染图表…', 'Rendering diagram…', '正在渲染图表…']) {
        view.rerender(<Markdown body={body} {...{ mermaidLoadingLabel: label }} />);
        expect(Array.from(view.container.querySelectorAll('.gl-prose-mermaid-loading'))).toEqual(fallbacks);
        expect(fallbacks.map(node => node.textContent)).toEqual([label, label]);
        expect(view.container.querySelector('.gl-prose')).toBe(prose);
        expect(screen.getByText('Rendering diagram…', { selector: 'p' })).toBe(paragraph);
        expect(view.container.querySelector('pre code')).toBe(code);
        expect(code?.textContent).toBe('Rendering diagram…\n正在渲染图表…\n');
      }
    } finally {
      await act(async () => chunk.release());
    }
    await waitFor(() => expect(view.container.querySelectorAll('svg')).toHaveLength(2));
    const diagrams = Array.from(view.container.querySelectorAll('svg'));
    const calls = vi.mocked(mermaid.render).mock.calls.length;
    for (const label of ['Rendering diagram…', '正在渲染图表…', 'Rendering diagram…']) {
      view.rerender(<Markdown body={body} {...{ mermaidLoadingLabel: label }} />);
      expect(Array.from(view.container.querySelectorAll('svg'))).toEqual(diagrams);
    }
    expect(vi.mocked(mermaid.render).mock.calls.length).toBe(calls);
  });

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
