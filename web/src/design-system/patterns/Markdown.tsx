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
