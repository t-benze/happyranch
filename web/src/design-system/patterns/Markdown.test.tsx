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
