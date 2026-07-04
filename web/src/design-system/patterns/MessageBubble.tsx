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
import React from 'react';
import { AgentChip } from './AgentChip';
import { Markdown } from './Markdown';
import { formatAttachmentSize } from '@/lib/threadAttachments';
import type { ThreadAttachment } from '@/lib/api/types';

export type MessageVariant = 'founder' | 'worker' | 'manager' | 'decline' | 'system';

interface MessageBubbleProps {
  variant: MessageVariant;
  seq: number;
  /** Speaker name; required for non-system variants. */
  speaker?: string;
  /** Role for the agent chip dot color. Founder/decline default to founder. */
  speakerRole?: 'manager' | 'worker' | 'founder';
  timestamp: string;
  /** Markdown body for `message`/`worker`/`manager`/`founder` variants. */
  body?: string | null;
  /** Used for `decline` variant. */
  declineReason?: string | null;
  /** Used for `system` variant — a pre-rendered one-line description. */
  systemDescription?: React.ReactNode;
  attachments?: ThreadAttachment[];
  /** Called when the user clicks an attachment chip. The owner wires this to
   *  an authed download action (e.g. artifactsApi.downloadArtifact or
   *  artifactsApi.downloadThreadAttachment). The full attachment object is
   *  passed so the handler can branch on thread_attachment_id vs artifact_name. */
  onAttachmentDownload?: (attachment: ThreadAttachment) => void;
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
        className="border-border-subtle text-caption text-text-muted mx-auto my-2 inline-flex max-w-fit items-center gap-2 self-center rounded-full border border-dashed bg-transparent px-3 py-1"
        aria-label="system event"
      >
        <span className="font-mono">[{props.seq}]</span>
        <span>{props.systemDescription ?? 'system event'}</span>
        <span className="text-text-muted">· {fmtTs(props.timestamp)}</span>
      </div>
    );
  }

  const {
    variant,
    seq,
    speaker,
    speakerRole,
    timestamp,
    body,
    declineReason,
    attachments,
    onAttachmentDownload,
  } = props;

  return (
    <article className={VARIANT_CONTAINER[variant]}>
      <header className="text-caption mb-1 flex items-baseline gap-2">
        {speaker && (
          <AgentChip
            name={speaker}
            role={speakerRole ?? (variant === 'founder' ? 'founder' : 'worker')}
          />
        )}
        <span className="text-text-muted ml-auto font-mono">
          #{seq} · {fmtTs(timestamp)}
        </span>
      </header>
      {variant === 'decline' ? (
        <p className="text-body text-tier-red">
          <strong>Declined:</strong> {declineReason}
        </p>
      ) : (
        <Markdown body={body ?? ''} />
      )}
      {variant !== 'decline' && attachments && attachments.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {attachments.map((attachment) => {
            const formattedSize = formatAttachmentSize(attachment.size_bytes);
            return (
              <button
                type="button"
                key={attachment.thread_attachment_id ?? attachment.artifact_name}
                onClick={() => onAttachmentDownload?.(attachment)}
                className="border-border-subtle bg-surface text-caption hover:bg-surface-hover inline-flex max-w-full items-center gap-2 rounded-md border px-2 py-1 cursor-pointer"
              >
                <span className="max-w-64 truncate">{attachment.display_name}</span>
                {formattedSize && (
                  <span className="text-text-muted shrink-0">{formattedSize}</span>
                )}
              </button>
            );
          })}
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
