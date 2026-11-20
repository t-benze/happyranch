/**
 * MessageBubble — single message in a thread transcript. Per DESIGN.md
 * `components.message_bubble`. Five variants:
 *   - `founder`  — right-aligned accent-soft bubble, no avatar, meta
 *                  (#seq · time) on the right (THR-376 mockup .mine)
 *   - `worker`   — surface card with border + shadow-sm, 4/16/16/16
 *                  radius, under a name · #seq · time meta row
 *                  (THR-376 mockup .msg .card-msg)
 *   - `manager`  — same as worker
 *   - `decline`  — red-tinted, prefixed "Declined:"
 *   - `system`   — dashed-border pill, centered, terse
 *
 * Agent cards cap at max-w-prose (65ch; mockup 68ch). The caller owns the
 * turn avatar beside agent turns; founder turns have none.
 *
 * Pure prop-driven. Markdown rendering is delegated to react-markdown.
 */
import React from 'react';
import { Markdown } from './Markdown';
import { formatAttachmentSize } from '@/lib/threadAttachments';
import type { ThreadAttachment } from '@/lib/api/types';

export type MessageVariant = 'founder' | 'worker' | 'manager' | 'decline' | 'system';

interface MessageBubbleProps {
  variant: MessageVariant;
  seq: number;
  /** Speaker name; required for non-system variants. */
  speaker?: string;
  speakerLabel?: React.ReactNode;
  /** Speaker role. Kept for API compatibility; the THR-376 meta row renders
   *  the name as text and no longer draws a role dot. */
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
  /**
   * Optional localized product copy (THR-118 W3a). Omitted fields keep the
   * historical English copy.
   */
  labels?: { declined?: string; systemEvent?: string; mermaidLoading?: string };
  /**
   * Optional display formatter for `timestamp` (e.g. an explicit-locale
   * formatter). Defaults to the historical host-locale `toLocaleString()`.
   */
  formatTimestamp?: (iso: string) => string;
  /**
   * Optional formatter for the full timestamp exposed as the time element's
   * `title` (hover) when `formatTimestamp` shows a shortened form. Defaults
   * to the historical host-locale `toLocaleString()`.
   */
  formatTimestampTitle?: (iso: string) => string;
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const VARIANT_CONTAINER: Record<MessageVariant, string> = {
  founder: '',
  worker: '',
  manager: '',
  decline: 'rounded-lg border border-tier-red bg-tier-red-tint p-4',
  // system handled separately
  system: '',
};

export function MessageBubble(props: MessageBubbleProps): JSX.Element {
  const formatTs = props.formatTimestamp ?? fmtTs;
  const formatTitle = props.formatTimestampTitle ?? fmtTs;
  if (props.variant === 'system') {
    return (
      <div
        className="border-border-subtle text-caption text-text-muted mx-auto my-2 inline-flex max-w-fit items-center gap-2 self-center rounded-full border border-dashed bg-transparent px-3 py-1"
        aria-label={props.labels?.systemEvent ?? 'system event'}
      >
        <span className="font-mono">[{props.seq}]</span>
        <span>{props.systemDescription ?? props.labels?.systemEvent ?? 'system event'}</span>
        <span className="text-text-muted">· {formatTs(props.timestamp)}</span>
      </div>
    );
  }

  const {
    variant,
    seq,
    speaker,
    timestamp,
    body,
    declineReason,
    attachments,
    onAttachmentDownload,
  } = props;

  const attachmentRow = variant !== 'decline' && attachments && attachments.length > 0 && (
    <div className={`mt-3 flex flex-wrap gap-2 ${variant === 'founder' ? 'justify-end' : ''}`}>
      {attachments.map((attachment) => {
        const formattedSize = formatAttachmentSize(attachment.size_bytes);
        return (
          <button
            type="button"
            key={attachment.thread_attachment_id ?? attachment.artifact_name}
            onClick={() => onAttachmentDownload?.(attachment)}
            className="border-border-subtle bg-surface-raised text-caption hover:bg-surface-hover inline-flex max-w-full items-center gap-2 rounded-md border px-2 py-1 cursor-pointer"
          >
            <span className="max-w-64 truncate">{attachment.display_name}</span>
            {formattedSize && (
              <span className="text-text-muted shrink-0">{formattedSize}</span>
            )}
          </button>
        );
      })}
    </div>
  );

  const time = (
    <time dateTime={timestamp} title={formatTitle(timestamp)}>
      {formatTs(timestamp)}
    </time>
  );
  const seqLabel = <span className="text-mono-sm font-mono tabular-nums">#{seq}</span>;

  // THR-376 thread detail (mockup .mine): the founder turn is a right-aligned
  // accent-soft bubble with no avatar and its meta (#seq · time) on the right.
  // The speaker name stays in the accessibility tree (sr-only) so the turn is
  // still announced with the current founder name.
  if (variant === 'founder') {
    return (
      <article className="flex min-w-0 flex-col items-end">
        <header className="text-caption text-text-secondary mb-1.25 flex items-baseline justify-end gap-2">
          {speaker && <span className="sr-only">{props.speakerLabel ?? speaker}</span>}
          {seqLabel}
          {time}
        </header>
        {/* min(56ch, 82%) composed from tokens: the wrapper caps at 80%, the
            bubble at max-w-md (28rem ≈ 56ch of 14px body text). */}
        <div className="flex w-full max-w-4/5 justify-end">
          <div className="bg-accent-soft text-text-primary min-w-0 max-w-md rounded-lg rounded-tr-sm px-3.75 py-2.5">
            <Markdown threadMentions body={body ?? ''} mermaidLoadingLabel={props.labels?.mermaidLoading} />
          </div>
        </div>
        {attachmentRow}
      </article>
    );
  }

  return (
    <article className={VARIANT_CONTAINER[variant] || undefined}>
      {/* THR-376 mockup .meta: name 13/600 · #seq mono 11 · time 12. */}
      <header className="text-caption text-text-secondary mb-1.25 flex min-w-0 items-baseline gap-2">
        {speaker && (
          <span className="text-body-sm text-text-primary min-w-0 truncate font-semibold">
            {props.speakerLabel ?? speaker}
          </span>
        )}
        {seqLabel}
        {time}
      </header>
      {variant === 'decline' ? (
        <p className="text-body text-tier-red max-w-prose">
          <strong>{props.labels?.declined ?? 'Declined:'}</strong> {declineReason}
        </p>
      ) : (
        // THR-376 mockup .card-msg: surface card, 1px border, 4/16/16/16
        // radius (nearest tokens: sm 8px tail, lg 18px), shadow-sm, 68ch cap
        // (nearest token: max-w-prose = 65ch).
        <div className="bg-surface-raised border-border-default shadow-pasture-sm text-body max-w-prose rounded-lg rounded-tl-sm border px-4 py-3">
          <Markdown threadMentions body={body ?? ''} mermaidLoadingLabel={props.labels?.mermaidLoading} />
        </div>
      )}
      {attachmentRow}
    </article>
  );
}
