/**
 * TypingBubble — an inline placeholder shown at the end of a conversation
 * transcript while an agent is composing (or queued to compose) its reply.
 *
 * Styled like a `worker` MessageBubble so it sits where the real reply will
 * land, with an animated three-dot "typing" indicator. Two states:
 *   - `working` — dots bounce, caption shows live elapsed (`replying… 5s`)
 *   - `queued`  — dots dimmed + static, caption `queued`
 *
 * Pure prop-driven. The owner computes the in-flight agent list and the shared
 * `nowMs` tick; this component only renders. Reused by the threads transcript
 * and the System Assistant dock (THR-056).
 */
import type { ReactNode } from 'react';
import { AgentChip } from './AgentChip';
import { formatElapsed } from '@/lib/elapsed';

export function TypingBubble({
  agentName,
  status,
  startedAt,
  nowMs,
  trailing,
}: {
  agentName: string;
  status: 'queued' | 'working';
  startedAt: string | null;
  nowMs?: number;
  /**
   * Optional inline control rendered at the far right of the header row, next
   * to the "replying…" caption (e.g. the threads "Abort reply" button). Omitted
   * by other consumers (System Assistant dock) so their layout is unchanged.
   */
  trailing?: ReactNode;
}): JSX.Element {
  const now = nowMs ?? Date.now();
  const working = status === 'working';
  const caption = working ? `replying… ${formatElapsed(startedAt, now)}`.trimEnd() : 'queued';

  return (
    <article
      className="border-border-subtle bg-surface-raised rounded-lg border p-4"
      aria-label={`${agentName} is ${working ? 'replying' : 'queued'}`}
    >
      <header className="text-caption mb-2 flex items-baseline gap-2">
        <AgentChip name={agentName} role="worker" />
        <span className="text-text-muted ml-auto">{caption}</span>
        {trailing}
      </header>
      <TypingDots animate={working} />
    </article>
  );
}

function TypingDots({ animate }: { animate: boolean }): JSX.Element {
  return (
    <div
      className={`text-text-muted flex items-center gap-1 ${animate ? '' : 'opacity-50'}`}
      aria-hidden="true"
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`inline-block h-2 w-2 rounded-full bg-current ${animate ? 'typing-dot' : ''}`}
        />
      ))}
    </div>
  );
}

export const meta = {
  name: "TypingBubble",
  layer: "pattern",
  import: "@/design-system/patterns/TypingBubble",
  variants: { status: ["queued", "working"] },
  consumes: [],
  example: "<TypingBubble agentName='engineering_head' status='working' startedAt={null} />",
} as const;
