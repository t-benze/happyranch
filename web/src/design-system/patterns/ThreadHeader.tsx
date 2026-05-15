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
  status: 'open' | 'archiving' | 'archived' | 'abandoned';
  participants: string[];
  turnsUsed: number;
  turnCap: number;
  archiveSummary?: string | null;
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
  actions,
}: ThreadHeaderProps): JSX.Element {
  return (
    <header className="border-b border-border-default bg-surface-sunken px-4 py-3">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
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
        <p className="mt-2 rounded-md border border-border-default bg-surface-raised p-2 text-caption text-text-muted">
          <strong className="text-text-primary">Archive summary:</strong>{' '}
          {archiveSummary}
        </p>
      )}
    </header>
  );
}
