/**
 * InboxRow — two-line row in the threads inbox. Per DESIGN.md
 * `components.inbox_row`. Pure prop-driven: the parent provides `href` (the
 * destination) and `onSelect` (the SPA-navigate callback); the row itself
 * doesn't know about react-router.
 *
 * Rendered as an `<a>` so middle-click / cmd-click open in a new tab,
 * right-click exposes "Copy link address", and assistive tech announces it
 * as a link. Plain primary clicks call `onSelect()` after suppressing the
 * default full-page reload, so the composition can drive SPA routing.
 *
 * Renders AgentChip for `lastSpeaker`, IdBadge for the thread id, and the
 * semantic status pill set (THREADS-05): an `active` pill for open threads, a
 * `done` pill for archived (terminal) threads, plus an additive `from dream`
 * pill when the thread was composed from a dream. The needs-you dot is a
 * leading 6px accent. The finer Direction-A states (waiting-on-you / review /
 * merged / live / idle) are intentionally absent — no field on the thread-list
 * payload backs them honestly.
 *
 * Direction-A Pasture card styling: bg-surface border-border-default rounded-lg
 * shadow-pasture-sm (ds.css .card). Active row uses accent-muted + left marker.
 */
import type { ReactNode } from 'react';
import { AgentChip } from './AgentChip';
import { CrescentMoonBadge } from './CrescentMoonBadge';
import { IdBadge } from './IdBadge';

interface InboxRowProps {
  threadId: string;
  subject: string;
  lastSpeaker?: { name: string; role: 'manager' | 'worker' | 'founder' };
  meta?: ReactNode;
  status: 'open' | 'archived';
  needsYou: boolean;
  active: boolean;
  /** Composed-from-dream marker — renders an additive "from dream" pill. */
  fromDream?: boolean;
  /** Destination URL for the row. Used as the `<a href>`. */
  href: string;
  /**
   * SPA-navigate handler. Invoked on plain primary clicks after
   * `preventDefault()`. Modifier clicks (cmd/ctrl/shift/middle/right) skip
   * `onSelect` and fall through to default anchor behaviour.
   */
  onSelect?: () => void;
}

export function InboxRow({
  threadId,
  subject,
  lastSpeaker,
  meta,
  status,
  needsYou,
  active,
  fromDream = false,
  href,
  onSelect,
}: InboxRowProps): JSX.Element {
  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented) return;
    if (e.button !== 0) return; // ignore non-primary clicks
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // open-in-new-tab et al.
    if (!onSelect) return;
    e.preventDefault();
    onSelect();
  };
  const statusLabel = status === 'open' ? 'active' : 'done';
  const statusPillCls =
    status === 'open'
      ? 'bg-accent-soft text-accent-text'
      : 'bg-surface-sunken border border-border-default text-text-muted';

  return (
    <a
      href={href}
      onClick={handleClick}
      aria-current={active ? 'page' : undefined}
      className={`group relative block w-full rounded-lg border px-3 py-2 text-left no-underline transition-colors ${
        active
          ? 'bg-accent-muted border-accent-muted shadow-pasture-sm'
          : 'bg-surface border-border-default shadow-pasture-sm hover:border-border-strong'
      }`}
    >
      {active && (
        <span
          aria-hidden="true"
          className="bg-accent absolute inset-y-1 left-0 w-0.5 rounded-full"
        />
      )}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {needsYou && (
            <span
              aria-label="needs you"
              className="bg-accent inline-block h-1.5 w-1.5 shrink-0 rounded-full"
            />
          )}
          <span className="text-body-sm text-text-primary truncate font-medium">
            {subject}
          </span>
        </div>
        <span className="flex shrink-0 items-center gap-1">
          {fromDream && (
            <span className="bg-accent-soft text-accent-text inline-flex items-center gap-1 rounded-full px-2 py-px text-xs leading-relaxed font-semibold">
              <CrescentMoonBadge className="h-3 w-3" />
              from dream
            </span>
          )}
          <span
            className={`inline-flex items-center rounded-full px-2 py-px text-xs leading-relaxed font-semibold ${statusPillCls}`}
          >
            {statusLabel}
          </span>
        </span>
      </div>
      <div className="text-caption text-text-muted mt-1 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <IdBadge id={threadId} kind="thread" />
          {lastSpeaker && (
            <>
              <span aria-hidden="true">·</span>
              <AgentChip name={lastSpeaker.name} role={lastSpeaker.role} />
            </>
          )}
        </div>
        {meta && <span className="shrink-0">{meta}</span>}
      </div>
    </a>
  );
}

export const meta = {
  name: "InboxRow",
  layer: "pattern",
  import: "@/design-system/patterns/InboxRow",
  variants: { status: ["open", "archived"] },
  consumes: ["components.inbox_row"],
  example: "<InboxRow threadId='THR-042' subject='Refund policy' status='open' needsYou={true} active={false} href='/orgs/demo/threads/THR-042' />",
} as const;
