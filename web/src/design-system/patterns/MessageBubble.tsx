/**
 * MessageBubble — single message in a thread transcript. Per DESIGN.md
 * `components.message_bubble`. Five variants:
 *   - `founder`  — accent-muted background, accent border
 *   - `worker`   — neutral raised background
 *   - `manager`  — neutral raised background (same as worker; the speaker
 *                  identity is rendered via AgentChip)
 *   - `decline`  — red-tinted, prefixed "Declined:"
 *   - `system`   — dashed-border pill, centered, terse
 *
 * Pure prop-driven. Markdown rendering is delegated to react-markdown.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AgentChip } from './AgentChip';

export type MessageVariant = 'founder' | 'worker' | 'manager' | 'decline' | 'system';

interface MessageBubbleProps {
  variant: MessageVariant;
  seq: number;
  /** Speaker name; required for non-system variants. */
  speaker?: string;
  /** Role for the agent chip dot color. Founder/decline default to founder. */
  speakerRole?: 'manager' | 'worker' | 'founder';
  addressedTo?: string[];
  timestamp: string;
  /** Markdown body for `message`/`worker`/`manager`/`founder` variants. */
  body?: string | null;
  /** Used for `decline` variant. */
  declineReason?: string | null;
  /** Used for `system` variant — a pre-rendered one-line description. */
  systemDescription?: string;
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const VARIANT_CONTAINER: Record<MessageVariant, string> = {
  founder: 'rounded-lg border border-accent-ring bg-accent-muted p-4',
  worker: 'rounded-lg border border-border-subtle bg-surface-raised p-4',
  manager: 'rounded-lg border border-border-subtle bg-surface-raised p-4',
  decline: 'rounded-lg border border-tier-red bg-tier-red-tint p-4',
  // system handled separately
  system: '',
};

export function MessageBubble(props: MessageBubbleProps): JSX.Element {
  if (props.variant === 'system') {
    return (
      <div
        className="mx-auto my-2 inline-flex max-w-fit items-center gap-2 self-center rounded-full border border-dashed border-border-subtle bg-transparent px-3 py-1 text-caption text-text-muted"
        aria-label="system event"
      >
        <span className="font-mono">[{props.seq}]</span>
        <span>{props.systemDescription ?? 'system event'}</span>
        <span className="text-text-muted">· {fmtTs(props.timestamp)}</span>
      </div>
    );
  }

  const { variant, seq, speaker, speakerRole, addressedTo, timestamp, body, declineReason } = props;

  return (
    <article className={VARIANT_CONTAINER[variant]}>
      <header className="mb-1 flex items-baseline gap-2 text-caption">
        {speaker && (
          <AgentChip
            name={speaker}
            role={speakerRole ?? (variant === 'founder' ? 'founder' : 'worker')}
          />
        )}
        {addressedTo && addressedTo.length > 0 && (
          <span className="text-text-muted">→ {addressedTo.join(', ')}</span>
        )}
        <span className="ml-auto font-mono text-text-muted">
          #{seq} · {fmtTs(timestamp)}
        </span>
      </header>
      {variant === 'decline' ? (
        <p className="text-body text-tier-red">
          <strong>Declined:</strong> {declineReason}
        </p>
      ) : (
        <div className="prose prose-invert prose-sm max-w-none text-text-primary">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {body ?? ''}
          </ReactMarkdown>
        </div>
      )}
    </article>
  );
}

export const meta = {
  name: "MessageBubble",
  layer: "pattern",
  import: "@/design-system/patterns/MessageBubble",
  variants: { variant: ["founder", "worker", "manager", "decline", "system"] },
  consumes: ["components.message_bubble"],
  example: "<MessageBubble variant='worker' seq={1} speaker='content_writer' speakerRole='worker' timestamp='2026-05-15T10:00:00Z' body='Drafted the post.' />",
} as const;
