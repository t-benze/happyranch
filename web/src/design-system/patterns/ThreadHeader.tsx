/**
 * ThreadHeader — the band above the message transcript. Per UI_SPEC §3.
 * Composes PageHeader + inline Pasture status pill + IdBadge + participant
 * list + actions.
 *
 * Direction-A Pasture: subject title uses --font-display (Newsreader serif),
 * status pill matches ds.css .tag pattern.
 *
 * Pure prop-driven. Actions are handed in as a slot so the composition can
 * compose Button primitives with its own onClick wiring.
 */
import type { ReactNode } from 'react';
import { CrescentMoonBadge } from './CrescentMoonBadge';
import { IdBadge } from './IdBadge';
import { PageHeader } from './PageHeader';

interface ThreadHeaderProps {
  threadId: string;
  subject: string;
  status: 'open' | 'archived';
  participants: string[];
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
  archiveSummary,
  dreamOriginated,
  actions,
}: ThreadHeaderProps): JSX.Element {
  const statusPillCls =
    status === 'open'
      ? 'bg-accent-soft text-accent-text'
      : 'bg-surface-sunken border border-border-default text-text-muted';

  return (
    <header className="border-border-default bg-surface-sunken border-b px-4 py-3">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            {dreamOriginated && <CrescentMoonBadge />}
            <span className="font-display truncate font-medium tracking-tight">{subject}</span>
            <span
              className={`inline-flex items-center rounded-full px-2 py-px text-xs leading-relaxed font-semibold ${statusPillCls}`}
            >
              {status === 'open' ? 'active' : 'archived'}
            </span>
          </span>
        }
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <IdBadge id={threadId} kind="thread" />
            <span aria-hidden="true">·</span>
            <span>{participants.join(', ') || 'no participants'}</span>

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
  example: "<ThreadHeader threadId='THR-042' subject='Refund policy' status='open' participants={['founder', 'compliance_head']} />",
} as const;
