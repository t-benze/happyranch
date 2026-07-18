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
   * Optional generic inline control rendered at the far right of the header
   * row, next to the "replying…" caption. Omitted by most consumers (e.g. the
   * System Assistant dock) so their layout is unchanged. Note: the threads
   * "Abort reply" control now lives inside the Composer input pill — thread
   * abort is no longer a TypingBubble `trailing` use.
   */
  trailing?: ReactNode;
}): JSX.Element {
  const now = nowMs ?? Date.now();
  const working = status === 'working';
  const caption = working ? `replying… ${formatElapsed(startedAt, now)}`.trimEnd() : 'queued';

  return (
    // Compact inline indicator (a-thread-detail `.replying`): a bold name row
    // with the "replying…" caption + an optional generic trailing control, above
    // a small chat-bubble that holds only the animated dots. No heavy card — the
    // sender avatar (TurnAvatar / dock) already carries identity beside it.
    <article
      className="min-w-0"
      aria-label={`${agentName} is ${working ? 'replying' : 'queued'}`}
    >
      <div className="flex items-center gap-2">
        <span className="text-fg truncate text-sm font-semibold">{agentName}</span>
        <span className={`text-caption ${working ? 'text-info' : 'text-text-muted'}`}>
          {caption}
        </span>
        {trailing ? <span className="ml-auto shrink-0">{trailing}</span> : null}
      </div>
      <div className="border-border-default bg-surface-sunken mt-2 inline-flex w-fit items-center rounded-lg border px-3.5 py-2.5">
        <TypingDots animate={working} />
      </div>
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
