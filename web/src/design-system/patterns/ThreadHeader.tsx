/**
 * ThreadHeader — the band above the message transcript. Per UI_SPEC §3.
 * Composes PageHeader + StatusBadge + IdBadge + participant list + actions.
 *
 * Pure prop-driven. Actions are handed in as a slot so the composition can
 * compose Button primitives with its own onClick wiring.
 */
import type { ReactNode } from 'react';
import { IdBadge } from './IdBadge';
import { PageHeader } from './PageHeader';
import { StatusBadge } from './StatusBadge';

interface ThreadHeaderProps {
  threadId: string;
  subject: string;
  status: 'open' | 'archived';
  participants: string[];
  turnsUsed: number;
  turnCap: number;
  archiveSummary?: string | null;
  /** When true, renders a crescent-moon badge (dream-originated marker, A4). */
  dreamOriginated?: boolean;
  actions?: ReactNode;
}

export function ThreadHeader({
  threadId,
  subject,
  status,
  participants,
  turnsUsed,
  turnCap,
  archiveSummary,
  dreamOriginated,
  actions,
}: ThreadHeaderProps): JSX.Element {
  return (
    <header className="border-border-default bg-surface-sunken border-b px-4 py-3">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            {dreamOriginated && (
              <svg
                className="text-accent inline-block shrink-0"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-label="Dream-originated thread"
                role="img"
              >
                <path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a6.4 6.4 0 0 1-4.54 1.86c-3.53 0-6.4-2.87-6.4-6.4 0-1.62.6-3.1 1.6-4.24A9 9 0 0 0 12 3Z" />
              </svg>
            )}
            <span className="truncate">{subject}</span>
            <StatusBadge status={status} />
          </span>
        }
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <IdBadge id={threadId} kind="thread" />
            <span aria-hidden="true">·</span>
            <span>{participants.join(', ') || 'no participants'}</span>
            <span aria-hidden="true">·</span>
            <span>
              {turnsUsed}/{turnCap} turns
            </span>
          </div>
        }
        actions={actions}
      />
      {archiveSummary && (
        <p className="border-border-default bg-surface-raised text-caption text-text-muted mt-2 rounded-md border p-2">
          <strong className="text-text-primary">Archive summary:</strong>{' '}
          {archiveSummary}
        </p>
      )}
    </header>
  );
}

export const meta = {
  name: "ThreadHeader",
  layer: "pattern",
  import: "@/design-system/patterns/ThreadHeader",
  variants: { status: ["open", "archived"] },
  consumes: ["layout.grid.threads_page"],
  example: "<ThreadHeader threadId='THR-042' subject='Refund policy' status='open' participants={['founder', 'compliance_head']} turnsUsed={3} turnCap={20} />",
} as const;
