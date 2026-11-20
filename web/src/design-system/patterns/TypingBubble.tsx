/**
 * TypingBubble — an inline placeholder shown at the end of a conversation
 * transcript while an agent is composing (or queued to compose) its reply.
 *
 * Styled like a `worker` MessageBubble card (THR-376) so it sits where the real reply will
 * land, with an animated three-dot "typing" indicator. Two states:
 *   - `working` — dots bounce, caption shows live elapsed (`replying… 5s`)
 *   - `queued`  — dots dimmed + static, caption `queued`
 *
 * Pure prop-driven. The owner computes the in-flight agent list and the shared
 * `nowMs` tick; this component only renders. Reused by the threads transcript
 */
import type { ReactNode } from 'react';
import { formatElapsed } from '@/lib/elapsed';

export function TypingBubble({
  agentName,
  agentLabel,
  status,
  startedAt,
  nowMs,
  trailing,
  caption,
  ariaLabel,
}: {
  agentName: string;
  agentLabel?: ReactNode;
  status: 'queued' | 'working';
  startedAt: string | null;
  nowMs?: number;
  /**
   * Optional generic inline control rendered at the far right of the header
   * row, next to the "replying…" caption. Omitted by most consumers (e.g. the
   * "Abort reply" control now lives inside the Composer input pill — thread
   * abort is no longer a TypingBubble `trailing` use.
   */
  trailing?: ReactNode;
  /**
   * Optional caption override (GH-688 Phase 1 Slice C). The threads
   * transcript drives the tail live indicator from the store-projected pair
   * state and passes an honest caption such as "queued · 4 messages coalesced
   * · messages 19–22" or "replying… 12s · messages 19–22". When omitted the
   * default "replying… <elapsed>" / "queued" caption is used, keeping the
   */
  caption?: string;
  /**
   * Optional accessible-name override (THR-118 W3a localized copy). When
   * omitted the historical English "<agent> is replying|queued" is used.
   */
  ariaLabel?: string;
}): JSX.Element {
  const now = nowMs ?? Date.now();
  const working = status === 'working';
  const defaultCaption = working
    ? `replying… ${formatElapsed(startedAt, now)}`.trimEnd()
    : 'queued';
  const shownCaption = caption ?? defaultCaption;

  return (
    // THR-376 mockup .msg.live: the same meta row as an agent turn (name
    // 13/600 + an info "replying…" caption) above a small card-styled bubble
    // (surface, border, 4/16/16/16 radius, shadow-sm) holding only the dots.
    <article
      className="min-w-0"
      aria-label={ariaLabel ?? `${agentName} is ${working ? 'replying' : 'queued'}`}
    >
      <div className="text-caption mb-1.25 flex min-w-0 items-baseline gap-2">
        <span className="text-body-sm text-text-primary min-w-0 truncate font-semibold">{agentLabel ?? agentName}</span>
        <span className={`min-w-0 ${working ? 'text-info font-semibold' : 'text-text-muted'}`}>
          {shownCaption}
        </span>
        {trailing ? <span className="ml-auto shrink-0">{trailing}</span> : null}
      </div>
      <div className="bg-surface-raised border-border-default shadow-pasture-sm inline-flex w-fit items-center rounded-lg rounded-tl-sm border px-3.75 py-2.75">
        <TypingDots animate={working} />
      </div>
    </article>
  );
}

function TypingDots({ animate }: { animate: boolean }): JSX.Element {
  return (
    <div
      className={`flex items-center gap-1 ${animate ? '' : 'opacity-50'}`}
      aria-hidden="true"
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`inline-block size-1.5 rounded-full bg-text-muted ${animate ? 'typing-dot' : ''}`}
        />
      ))}
    </div>
  );
}
